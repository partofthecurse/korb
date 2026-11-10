import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyotp
from cryptography.fernet import Fernet

from shoppinglist.amazon import AmazonRemote, get_amazon_credentials
from shoppinglist.security import hasher, load_amazon_cipher
from shoppinglist.web import create_app, jittered_interval


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({"TESTING": True, "DATA_DIR": self.temp.name, "ENABLE_SCHEDULER": False,
                               "SESSION_COOKIE_SECURE": False, "WTF_CSRF_ENABLED": False})
        self.store = self.app.extensions["shopping_store"]
        with self.store.connect() as connection:
            connection.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                               ("admin", hasher.hash("admin-password-123"), "admin"))
            connection.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                               ("user", hasher.hash("user-password-123"), "user"))
        self.client = self.app.test_client()

    def login(self, username="admin", password="admin-password-123"):
        return self.client.post("/login", data={"username": username, "password": password})

    def test_api_requires_login_and_user_cannot_manage_accounts(self):
        self.assertEqual(self.client.get("/api/items").status_code, 401)
        self.login("user", "user-password-123")
        self.assertEqual(self.client.get("/users").status_code, 403)
        self.assertEqual(self.client.post("/api/items", json={"name": "Milch"}).status_code, 201)
        self.assertEqual(self.client.get("/api/items").json["items"][0]["name"], "Milch")

    def test_sync_button_is_hidden_until_amazon_credentials_exist(self):
        self.login()
        response = self.client.get("/")
        self.assertNotIn(b'id="sync-button"', response.data)
        self.assertIn(b"Amazon-Synchronisierung nicht eingerichtet", response.data)

    def test_sync_interval_has_bounded_positive_jitter(self):
        with patch("shoppinglist.web.random.uniform", return_value=17):
            self.assertEqual(jittered_interval(300), 317)
        with patch("shoppinglist.web.random.uniform", return_value=30):
            self.assertEqual(jittered_interval(300), 330)

    def test_duplicate_item_and_quantity_stepper(self):
        self.login()
        first = self.client.post("/api/items", json={"name": "Milch"})
        second = self.client.post("/api/items", json={"name": " milch "})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json["id"], second.json["id"])
        self.assertEqual(second.json["quantity"], 2)
        item_id = first.json["id"]
        self.assertEqual(self.client.patch(f"/api/items/{item_id}", json={"quantity_delta": 1}).json["quantity"], 3)
        self.assertEqual(self.client.patch(f"/api/items/{item_id}", json={"quantity_delta": -1}).json["quantity"], 2)
        self.assertEqual(self.client.get("/api/items").json["items"][0]["quantity"], 2)

    def test_account_disable_invalidates_existing_session(self):
        self.login("user", "user-password-123")
        with self.store.connect() as connection:
            connection.execute("UPDATE users SET active = 0, epoch = epoch + 1 WHERE username = 'user'")
        self.assertEqual(self.client.get("/api/items").status_code, 401)

    def test_last_admin_cannot_be_disabled(self):
        self.login()
        self.client.post("/users/1/toggle")
        self.client.post("/users/1/delete")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT active FROM users WHERE id = 1").fetchone()[0], 1)

    def test_admin_can_delete_user(self):
        self.login()
        self.client.post("/users/2/delete")
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute("SELECT id FROM users WHERE id = 2").fetchone())

    def test_totp_setup_and_one_time_recovery(self):
        self.login()
        self.client.post("/account", data={"action": "start"})
        with self.store.connect() as connection:
            encrypted = connection.execute("SELECT totp_pending FROM users WHERE id = 1").fetchone()[0]
        cipher = Fernet((Path(self.app.config["DATA_DIR"]) / "totp.key").read_bytes())
        secret = cipher.decrypt(encrypted.encode()).decode()
        response = self.client.post("/account", data={"action": "confirm", "code": pyotp.TOTP(secret).now()})
        codes = re.findall(rb"<code>([0-9A-F]{10})</code>", response.data)
        self.assertEqual(len(codes), 8)
        self.client.post("/logout")
        self.login()
        self.assertEqual(self.client.get("/api/items").status_code, 401)
        self.client.post("/login/2fa", data={"code": codes[0].decode()})
        self.assertEqual(self.client.get("/api/items").status_code, 200)
        self.client.post("/logout")
        self.login()
        self.client.post("/login/2fa", data={"code": codes[0].decode()})
        self.assertEqual(self.client.get("/api/items").status_code, 401)

    def test_csrf_required_for_login(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.login().status_code, 400)

    def test_encrypted_settings_are_not_stored_as_plaintext(self):
        cipher = load_amazon_cipher(self.temp.name)
        secret = "private-amazon-password"
        encrypted = cipher.encrypt(secret.encode()).decode()
        self.store.set_settings({"amazon_password": encrypted})
        self.assertEqual(cipher.decrypt(self.store.get_setting("amazon_password").encode()).decode(), secret)
        with self.store.path.open("rb") as database:
            self.assertNotIn(secret.encode(), database.read())

    def test_amazon_settings_are_admin_only_and_write_only(self):
        self.login("user", "user-password-123")
        self.assertEqual(self.client.get("/settings/amazon").status_code, 403)
        self.client.post("/logout")
        self.login()
        response = self.client.post("/settings/amazon", data={
            "email": "amazon@example.com",
            "amazon_password": "amazon-private-password",
            "totp_secret": pyotp.random_base32(),
            "current_password": "admin-password-123",
        })
        self.assertEqual(response.status_code, 302)
        credentials = get_amazon_credentials(
            self.store, self.app.extensions["shopping_amazon_cipher"]
        )
        self.assertEqual(credentials["amazon_email"], "amazon@example.com")
        self.assertEqual(credentials["amazon_password"], "amazon-private-password")
        self.assertEqual(self.store.get_setting("amazon_domain"), "amazon.com")
        page = self.client.get("/settings/amazon")
        self.assertNotIn(b"amazon-private-password", page.data)
        self.assertIn(b'id="sync-button"', self.client.get("/").data)
        with self.store.path.open("rb") as database:
            database_bytes = database.read()
        self.assertNotIn(b"amazon-private-password", database_bytes)
        self.assertNotIn(b"amazon@example.com", database_bytes)

    def test_amazon_settings_require_current_password(self):
        self.login()
        response = self.client.post("/settings/amazon", data={
            "email": "amazon@example.com",
            "amazon_password": "amazon-private-password",
            "totp_secret": pyotp.random_base32(),
            "current_password": "incorrect-password",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.store.get_setting("amazon_email"))

    def test_amazon_settings_require_totp_when_enabled(self):
        self.login()
        amazon_totp = pyotp.random_base32()
        cipher = Fernet((__import__("pathlib").Path(self.app.config["DATA_DIR"]) / "totp.key").read_bytes())
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE users SET totp_secret = ? WHERE username = 'admin'",
                (cipher.encrypt(pyotp.random_base32().encode()).decode(),),
            )
        response = self.client.post("/settings/amazon", data={
            "email": "amazon@example.com",
            "amazon_password": "amazon-private-password",
            "totp_secret": amazon_totp,
            "current_password": "admin-password-123",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.store.get_setting("amazon_email"))

    def test_amazon_settings_reject_invalid_totp_secret(self):
        self.login()
        response = self.client.post("/settings/amazon", data={
            "email": "amazon@example.com",
            "amazon_password": "amazon-private-password",
            "totp_secret": "not-a-valid-base32-secret",
            "current_password": "admin-password-123",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.store.get_setting("amazon_email"))

    def test_amazon_settings_reject_unsupported_region(self):
        self.login()
        response = self.client.post("/settings/amazon", data={
            "amazon_domain": "amazon.invalid",
            "current_password": "admin-password-123",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.store.get_setting("amazon_domain"))

    def test_amazon_session_is_encrypted_and_legacy_file_is_migrated(self):
        cipher = load_amazon_cipher(self.temp.name)
        remote = AmazonRemote(self.temp.name, self.store, cipher)
        session = {"refresh_token": "private-amazon-refresh-token", "site": "amazon.de"}
        legacy_path = Path(self.temp.name) / "amazon-login.json"
        legacy_path.write_text(json.dumps(session), encoding="utf-8")
        self.assertEqual(remote._load_login_data(), session)
        self.assertFalse(legacy_path.exists())
        with self.store.path.open("rb") as database:
            database_bytes = database.read()
        self.assertNotIn(b"private-amazon-refresh-token", database_bytes)
        encrypted = self.store.get_setting("amazon_login")
        self.assertEqual(json.loads(cipher.decrypt(encrypted.encode())), session)

    def test_disconnect_clears_credentials_and_session_overrides_env(self):
        cipher = self.app.extensions["shopping_amazon_cipher"]
        self.store.set_settings({
            "amazon_email": cipher.encrypt(b"stored@example.com").decode(),
            "amazon_password": cipher.encrypt(b"stored-password").decode(),
            "amazon_totp_secret": cipher.encrypt(pyotp.random_base32().encode()).decode(),
            "amazon_login": cipher.encrypt(b"{} ").decode(),
        })
        legacy_path = Path(self.app.config["DATA_DIR"]) / "amazon-login.json"
        legacy_path.write_text("{}", encoding="utf-8")
        self.login()
        with patch.dict("os.environ", {
            "AMAZON_EMAIL": "environment@example.com",
            "AMAZON_PASSWORD": "environment-password",
            "AMAZON_TOTP_SECRET": pyotp.random_base32(),
        }):
            response = self.client.post("/settings/amazon", data={
                "clear_credentials": "yes",
                "current_password": "admin-password-123",
            })
            self.assertEqual(response.status_code, 302)
            credentials = get_amazon_credentials(self.store, cipher)
        self.assertEqual(credentials, {"amazon_email": "", "amazon_password": "", "amazon_totp_secret": ""})
        self.assertIsNone(self.store.get_setting("amazon_login"))
        self.assertFalse(legacy_path.exists())

    def test_failed_logins_are_rate_limited(self):
        for _ in range(5):
            self.login("user", "wrong-password")
        response = self.login("user", "user-password-123")
        self.assertIn("Zu viele Versuche".encode(), response.data)
        self.assertEqual(self.client.get("/api/items").status_code, 401)


if __name__ == "__main__":
    unittest.main()

class FoodCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({"TESTING": True, "DATA_DIR": self.temp.name, "ENABLE_SCHEDULER": False,
                               "SESSION_COOKIE_SECURE": False, "WTF_CSRF_ENABLED": False})
        self.store = self.app.extensions["shopping_store"]
        with self.store.connect() as connection:
            connection.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                               ("admin", hasher.hash("admin-password-123"), "admin"))
            connection.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                               ("user", hasher.hash("user-password-123"), "user"))
        self.client = self.app.test_client()

    def login(self, username="admin", password="admin-password-123"):
        return self.client.post("/login", data={"username": username, "password": password})

    def describe(self, name):
        self.client.post("/api/items", json={"name": name})
        return next(item for item in self.client.get("/api/items").json["items"] if item["name"] == name)

    def test_items_get_icon_and_department_from_catalog(self):
        self.login("user", "user-password-123")
        cases = {
            "Ananas": ("1f34d", "Obst und Gemüse"),
            "Ananassaft": ("1f9c3", "Limo und Bier"),
            "Eier": ("1f95a", "Brot und Eier"),
            "Tomatenmark": ("1f345", "Pasta und Tomatensachen"),
            "Erdbeerjoghurt": ("1f95b", "Milchprodukte"),
            "Kartoffelchips": ("1f954", "Snacks und Süßes"),
            "Brei": ("1f6d2", "Sonstiges"),
        }
        for name, (icon, category) in cases.items():
            item = self.describe(name)
            self.assertEqual((item["icon"], item["category"]), (icon, category), name)
        self.assertIn("Sonstiges", self.client.get("/api/items").json["categories"])

    def test_catalog_is_admin_only(self):
        self.login("user", "user-password-123")
        self.assertEqual(self.client.get("/foods").status_code, 403)
        self.assertEqual(self.client.post("/foods", data={"name": "X"}).status_code, 403)

    def test_admin_adds_edits_and_deletes_food(self):
        self.login()
        self.client.post("/api/items", json={"name": "Physalis"})
        page = self.client.get("/foods")
        self.assertIn("Physalis".encode(), page.data)
        self.assertIn("NICHT ZUGEORDNET".encode(), page.data)
        response = self.client.post("/foods", data={"name": "Physalis", "terms": " kapstachelbeere ,, ",
                                                    "icon": "1f34a", "category": "Obst und Gemüse", "color": "coral"})
        self.assertEqual(response.status_code, 302)
        item = self.describe("Physalis")
        self.assertEqual((item["icon"], item["category"], item["color"]), ("1f34a", "Obst und Gemüse", "coral"))
        food = next(food for food in self.store.list_foods() if food["name"] == "Physalis")
        self.assertEqual(food["terms"], "kapstachelbeere")
        duplicate = self.client.post("/foods", data={"name": "physalis", "icon": "1f34a",
                                                     "category": "Obst und Gemüse", "color": "coral"})
        self.assertEqual(duplicate.status_code, 409)
        self.client.post("/foods", data={"food_id": food["id"], "name": "Physalis", "terms": "",
                                         "icon": "1f34b", "category": "Sonstiges", "color": "yellow"})
        self.assertEqual(self.describe("Physalis")["category"], "Sonstiges")
        self.assertEqual(self.client.get(f"/foods?edit={food['id']}").status_code, 200)
        invalid = self.client.post("/foods", data={"name": "Y", "icon": "../evil", "category": "Sonstiges", "color": "blue"})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.post(f"/foods/{food['id']}/delete").status_code, 302)
        self.assertIsNone(self.describe("Physalis")["food_id"])

    def test_restore_adds_only_missing_defaults(self):
        self.login()
        ananas = next(food for food in self.store.list_foods() if food["name"] == "Ananas")
        count = len(self.store.list_foods())
        self.client.post(f"/foods/{ananas['id']}/delete")
        self.assertEqual(self.store.restore_default_foods(), 1)
        self.assertEqual(len(self.store.list_foods()), count)
        self.assertEqual(self.store.restore_default_foods(), 0)

    def test_every_default_icon_exists(self):
        icons_dir = Path(self.app.static_folder) / "icons"
        for food in self.store.list_foods():
            self.assertTrue((icons_dir / f"{food['icon']}.svg").exists(), food["name"])

    def test_departments_can_be_added_reordered_renamed_and_deleted(self):
        self.login()
        self.assertEqual(self.client.post("/categories", data={"name": "Drogerie"}).status_code, 302)
        self.assertEqual(self.client.post("/categories", data={"name": " drogerie "}).status_code, 302)
        names = self.store.category_names()
        self.assertEqual(names.count("Drogerie"), 1)
        self.assertEqual(names[-1], "Drogerie")
        drogerie = next(c for c in self.store.list_categories() if c["name"] == "Drogerie")
        self.client.post(f"/categories/{drogerie['id']}/top")
        self.client.post(f"/categories/{drogerie['id']}/down")
        self.assertEqual(self.store.category_names()[1], "Drogerie")
        self.assertEqual(self.client.get("/api/items").json["categories"][1], "Drogerie")

        self.client.post("/foods", data={"name": "Haarkur", "terms": "haarmaske", "icon": "1f9f4",
                                         "category": "Drogerie", "color": "blue"})
        self.assertEqual(self.describe("Haarkur")["category"], "Drogerie")
        self.client.post(f"/categories/{drogerie['id']}/rename", data={"name": "Drogerie & Pflege"})
        self.assertEqual(self.describe("Haarkur")["category"], "Drogerie & Pflege")
        self.client.post(f"/categories/{drogerie['id']}/delete")
        self.assertNotIn("Drogerie & Pflege", self.store.category_names())
        self.assertEqual(self.describe("Haarkur")["category"], "Sonstiges")

    def test_fallback_department_is_protected_and_users_cannot_manage(self):
        self.login()
        fallback = next(c for c in self.store.list_categories() if c["name"] == "Sonstiges")
        self.client.post(f"/categories/{fallback['id']}/delete")
        self.client.post(f"/categories/{fallback['id']}/rename", data={"name": "Rest"})
        self.assertIn("Sonstiges", self.store.category_names())
        self.assertEqual(self.client.post(f"/categories/{fallback['id']}/explode").status_code, 404)
        self.assertEqual(self.client.post("/categories/9999/up").status_code, 404)
        self.client.post("/logout")
        self.login("user", "user-password-123")
        self.assertEqual(self.client.post("/categories", data={"name": "X"}).status_code, 403)
