import asyncio
import base64
import io
import os
import random
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import pyotp
import qrcode
from aioamazondevices.exceptions import CannotAuthenticate, CannotRetrieveData
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .catalog import CATEGORIES, COLORS, ICON_LABELS, Matcher, available_icons
from .amazon import AMAZON_DOMAINS, AMAZON_SETTINGS, AmazonRemote, get_amazon_credentials
from .security import DUMMY_HASH, clear_failures, hasher, load_amazon_cipher, load_keys, password_valid, rate_limited, record_failure, recovery_hash
from .store import Store
from .sync import Synchronizer


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.role = row["role"]
        self.epoch = row["epoch"]
        self.totp_enabled = bool(row["totp_secret"])

    def get_id(self):
        return f"{self.id}:{self.epoch}"


class SyncRunner:
    def __init__(self, store, data_dir, amazon_cipher):
        self.store = store
        self.data_dir = data_dir
        self.amazon_cipher = amazon_cipher
        self.lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.status = {"running": False, "last_success": None, "error": None}

    def start(self):
        if not self.lock.acquire(blocking=False):
            return False
        with self.status_lock:
            self.status["running"] = True
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        async def run():
            async with AmazonRemote(self.data_dir, self.store, self.amazon_cipher) as remote:
                await Synchronizer(self.store, remote).run()

        try:
            asyncio.run(run())
            with self.status_lock:
                self.status.update(last_success=datetime.now(timezone.utc).isoformat(), error=None)
        except CannotAuthenticate:
            with self.status_lock:
                self.status["error"] = "Amazon-Anmeldung fehlgeschlagen. Zugangsdaten und Sicherheitsabfragen prüfen."
        except CannotRetrieveData as error:
            with self.status_lock:
                if "Not Found" in str(error):
                    self.status["error"] = "Amazon-Endpunkt nicht gefunden. Bitte die Amazon-Region unter Einstellungen > Amazon prüfen."
                else:
                    self.status["error"] = "Amazon konnte die Liste nicht laden. Region und Kontozugang prüfen."
        except Exception as error:
            with self.status_lock:
                if isinstance(error, RuntimeError) and str(error) == "Amazon credentials are not configured":
                    self.status["error"] = "Amazon-Zugangsdaten fehlen."
                elif isinstance(error, RuntimeError) and str(error) == "The Amazon shopping list response is incomplete":
                    self.status["error"] = "Amazon liefert eine unvollständige Liste. Synchronisierung wird wiederholt."
                else:
                    self.status["error"] = f"Amazon-Synchronisierung fehlgeschlagen ({type(error).__name__})."
        finally:
            with self.status_lock:
                self.status["running"] = False
            self.lock.release()

    def schedule(self, interval):
        def loop():
            while True:
                self.start()
                time.sleep(jittered_interval(interval))
        threading.Thread(target=loop, daemon=True).start()

    def snapshot(self):
        with self.status_lock:
            return dict(self.status)


def jittered_interval(interval):
    return interval + random.uniform(0, min(30, interval * 0.1))


def create_app(test_config=None):
    os.umask(0o077)
    app = Flask(__name__)
    app.config.update(
        DATA_DIR=os.getenv("SHOPPING_DATA_DIR", "/data"),
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=64 * 1024,
        ENABLE_SCHEDULER=os.getenv("ENABLE_SCHEDULER", "true").lower() == "true",
    )
    if test_config:
        app.config.update(test_config)
    data_dir = Path(app.config["DATA_DIR"])
    data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    app.secret_key, cipher = load_keys(data_dir)
    amazon_cipher = load_amazon_cipher(data_dir)
    store = Store(data_dir / "shopping.db")
    runner = SyncRunner(store, data_dir, amazon_cipher)
    app.extensions["shopping_store"] = store
    app.extensions["shopping_sync"] = runner
    app.extensions["shopping_amazon_cipher"] = amazon_cipher
    CSRFProtect(app)
    login_manager = LoginManager(app)
    login_manager.login_view = "login"
    if os.getenv("TRUST_PROXY", "false").lower() == "true":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=0, x_for=0)

    @login_manager.user_loader
    def load_user(identifier):
        try:
            user_id, epoch = (int(value) for value in identifier.split(":"))
        except (ValueError, AttributeError):
            return None
        with store.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ? AND active = 1 AND epoch = ?", (user_id, epoch)).fetchone()
        return User(row) if row else None

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify(error="Anmeldung erforderlich"), 401
        return redirect(url_for("login"))

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role != "admin":
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def subjects(username):
        return ["user:" + username.casefold(), "ip:" + (request.remote_addr or "unknown")]

    def verify_second_factor(user_id, code):
        code = code.strip().upper()
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT totp_secret, last_totp_step FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
            if not user or not user["totp_secret"]:
                return False
            totp = pyotp.TOTP(cipher.decrypt(user["totp_secret"].encode()).decode())
            current_step = int(time.time()) // totp.interval
            for step in (current_step - 1, current_step, current_step + 1):
                if step > user["last_totp_step"] and secrets.compare_digest(totp.at(step * totp.interval), code):
                    connection.execute("UPDATE users SET last_totp_step = ? WHERE id = ?", (step, user_id))
                    return True
            return bool(connection.execute(
                "DELETE FROM recovery_codes WHERE user_id = ? AND code_hash = ?",
                (user_id, recovery_hash(code)),
            ).rowcount)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()[:80]
            password = request.form.get("password", "")[:1024]
            keys = subjects(username)
            if rate_limited(store, keys):
                flash("Zu viele Versuche. Bitte später erneut versuchen.", "error")
            else:
                with store.connect() as connection:
                    row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                valid = password_valid(password, row["password_hash"] if row else DUMMY_HASH)
                if not row or not row["active"] or not valid:
                    record_failure(store, keys)
                    flash("Anmeldung fehlgeschlagen.", "error")
                elif row["totp_secret"]:
                    session.clear()
                    session["pending_user"] = row["id"]
                    session["pending_epoch"] = row["epoch"]
                    session["pending_since"] = int(time.time())
                    return redirect(url_for("login_otp"))
                else:
                    clear_failures(store, keys)
                    session.clear()
                    login_user(User(row))
                    session.permanent = True
                    return redirect(url_for("index"))
        return render_template("login.html", otp=False)

    @app.route("/login/2fa", methods=["GET", "POST"])
    def login_otp():
        user_id = session.get("pending_user")
        if not user_id or int(time.time()) - session.get("pending_since", 0) > 300:
            session.clear()
            return redirect(url_for("login"))
        with store.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ? AND active = 1 AND epoch = ?", (user_id, session.get("pending_epoch"))).fetchone()
        if not row:
            session.clear()
            return redirect(url_for("login"))
        if request.method == "POST":
            keys = subjects(row["username"])
            if rate_limited(store, keys):
                flash("Zu viele Versuche. Bitte später erneut versuchen.", "error")
            elif verify_second_factor(user_id, request.form.get("code", "")):
                clear_failures(store, keys)
                session.clear()
                login_user(User(row))
                session.permanent = True
                return redirect(url_for("index"))
            else:
                record_failure(store, keys)
                flash("Anmeldung fehlgeschlagen.", "error")
        return render_template("login.html", otp=True)

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index():
        amazon_configured = all(get_amazon_credentials(store, amazon_cipher).values())
        return render_template("index.html", amazon_configured=amazon_configured)

    @app.get("/api/items")
    @login_required
    def items():
        matcher = Matcher(store.list_foods())
        entries = [{**item, **matcher.describe(item["name"])} for item in store.list_items()]
        return jsonify(items=entries, categories=CATEGORIES, sync=runner.snapshot())

    @app.post("/api/items")
    @login_required
    def add_item():
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            return jsonify(error="Ungültiger Artikelname"), 400
        item_id, quantity, created, updated = store.add_item(name.strip())
        if not updated:
            return jsonify(error="Die Menge kann höchstens 99 betragen."), 409
        return jsonify(id=item_id, quantity=quantity, created=created), 201 if created else 200

    @app.patch("/api/items/<int:item_id>")
    @login_required
    def update_item(item_id):
        data = request.get_json(silent=True) or {}
        if isinstance(data.get("completed"), bool):
            if not store.complete_item(item_id, data["completed"]):
                abort(404)
            return "", 204
        delta = data.get("quantity_delta")
        if not isinstance(delta, int) or isinstance(delta, bool) or delta not in (-1, 1):
            abort(400)
        quantity = store.change_quantity(item_id, delta)
        if quantity is None:
            abort(404)
        return jsonify(quantity=quantity)

    @app.delete("/api/items/<int:item_id>")
    @login_required
    def remove_item(item_id):
        if not store.delete_item(item_id):
            abort(404)
        return "", 204

    @app.post("/api/sync")
    @login_required
    def sync_now():
        return jsonify(started=runner.start()), 202

    @app.route("/settings/amazon", methods=["GET", "POST"])
    @admin_required
    def amazon_settings():
        amazon_keys = tuple(AMAZON_SETTINGS)
        if request.method == "POST":
            with store.connect() as connection:
                admin = connection.execute("SELECT * FROM users WHERE id = ? AND active = 1", (current_user.id,)).fetchone()
            keys = subjects(current_user.username)
            limited = rate_limited(store, keys)
            password_ok = password_valid(request.form.get("current_password", ""), admin["password_hash"])
            code_ok = not admin["totp_secret"] or (password_ok and not limited and verify_second_factor(
                current_user.id, request.form.get("current_code", "")
            ))
            if limited or not password_ok or not code_ok:
                record_failure(store, keys)
                flash("Bestätigung fehlgeschlagen. Zugangsdaten wurden nicht geändert.", "error")
                return redirect(url_for("amazon_settings"))

            if request.form.get("clear_credentials") == "yes":
                values = {key: "" for key in amazon_keys}
            else:
                values = get_amazon_credentials(store, amazon_cipher)
                domain = request.form.get(
                    "amazon_domain",
                    store.get_setting("amazon_domain") or os.getenv("AMAZON_DOMAIN", "amazon.com"),
                ).strip().lower()
                email = request.form.get("email", "").strip()
                password = request.form.get("amazon_password", "")
                totp_secret = "".join(request.form.get("totp_secret", "").split())
                if email:
                    values["amazon_email"] = email
                if password:
                    values["amazon_password"] = password
                if totp_secret:
                    values["amazon_totp_secret"] = totp_secret

                any_value = any(values.values())
                all_values = all(values.values())
                valid_email = "@" in values["amazon_email"] and len(values["amazon_email"]) <= 254
                valid_totp = False
                if values["amazon_totp_secret"]:
                    try:
                        pyotp.TOTP(values["amazon_totp_secret"]).now()
                        valid_totp = True
                    except (ValueError, TypeError):
                        pass
                if domain not in AMAZON_DOMAINS:
                    flash("Bitte eine unterstützte Amazon-Region auswählen.", "error")
                    return redirect(url_for("amazon_settings"))
                if any_value and (not all_values or not valid_email or not valid_totp):
                    flash("Bitte eine gültige Amazon-E-Mail, ein Passwort und ein gültiges TOTP-Geheimnis angeben.", "error")
                    return redirect(url_for("amazon_settings"))

            encrypted = {
                key: amazon_cipher.encrypt(value.encode()).decode()
                for key, value in values.items()
            }
            store.set_settings(encrypted)
            if request.form.get("clear_credentials") != "yes":
                store.set_settings({"amazon_domain": domain})
            store.delete_settings(["amazon_login"])
            (data_dir / "amazon-login.json").unlink(missing_ok=True)
            clear_failures(store, keys)
            flash("Amazon-Zugangsdaten wurden verschlüsselt gespeichert. Die nächste Synchronisierung meldet das Konto neu an.", "success")
            return redirect(url_for("amazon_settings"))

        configured = all(get_amazon_credentials(store, amazon_cipher).values())
        stored = configured and all(store.get_setting(key) is not None for key in amazon_keys)
        amazon_domain = store.get_setting("amazon_domain") or os.getenv("AMAZON_DOMAIN", "amazon.com")
        return render_template(
            "amazon_settings.html",
            configured=configured,
            stored=stored,
            totp_required=current_user.totp_enabled,
            amazon_domain=amazon_domain,
            amazon_domains=AMAZON_DOMAINS,
        )

    icons = available_icons(app.static_folder)

    @app.route("/foods", methods=["GET", "POST"])
    @admin_required
    def foods():
        if request.method == "POST":
            form = {key: request.form.get(key, "").strip() for key in ("name", "terms", "icon", "category", "color")}
            form["terms"] = ", ".join(term.strip() for term in form["terms"].split(",") if term.strip())
            food_id = request.form.get("food_id", type=int)
            if (not 1 <= len(form["name"]) <= 100 or len(form["terms"]) > 1000 or form["icon"] not in icons
                    or form["category"] not in CATEGORIES or form["color"] not in COLORS):
                flash("Bitte Namen, Abteilung, Farbe und Icon angeben.", "error")
                return render_foods(form, food_id), 400
            if not store.save_food(food_id, **form):
                flash("Diesen Eintrag gibt es schon – bitte den vorhandenen bearbeiten.", "error")
                return render_foods(form, food_id), 409
            flash(f"„{form['name']}“ gespeichert.", "success")
            return redirect(url_for("foods"))
        edit_id = request.args.get("edit", type=int)
        form = next((food for food in store.list_foods() if food["id"] == edit_id), None)
        if form is None:
            edit_id = None
            name = request.args.get("name", "").strip()[:100]
            form = {"name": name, "terms": "", "icon": "", "category": "", "color": ""}
            match = Matcher(store.list_foods()).match(name) if name else None
            if match:
                form.update(icon=match["icon"], category=match["category"], color=match["color"])
        return render_foods(form, edit_id)

    def render_foods(form, edit_id):
        catalog = store.list_foods()
        matcher = Matcher(catalog)
        unmatched = sorted({item["name"] for item in store.list_items() if matcher.match(item["name"]) is None}, key=str.casefold)
        grouped = {category: [food for food in catalog if food["category"] == category] for category in CATEGORIES}
        return render_template(
            "foods.html", form=form, edit_id=edit_id, grouped=grouped, total=len(catalog), unmatched=unmatched,
            categories=CATEGORIES, colors=COLORS, icons=icons, icon_labels=ICON_LABELS,
        )

    @app.post("/foods/<int:food_id>/delete")
    @admin_required
    def delete_food(food_id):
        if not store.delete_food(food_id):
            abort(404)
        flash("Eintrag gelöscht.", "success")
        return redirect(url_for("foods"))

    @app.post("/foods/restore")
    @admin_required
    def restore_foods():
        added = store.restore_default_foods()
        flash(f"{added} Standardeinträge ergänzt." if added else "Alle Standardeinträge sind bereits vorhanden.", "success")
        return redirect(url_for("foods"))

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            action = request.form.get("action")
            with store.connect() as connection:
                row = connection.execute("SELECT * FROM users WHERE id = ?", (current_user.id,)).fetchone()
            if action == "password":
                keys = subjects(current_user.username)
                new_password = request.form.get("new_password", "")
                if not rate_limited(store, keys) and len(new_password) >= 12 and password_valid(request.form.get("current_password", ""), row["password_hash"]):
                    with store.connect() as connection:
                        connection.execute("UPDATE users SET password_hash = ?, epoch = epoch + 1 WHERE id = ?", (hasher.hash(new_password), current_user.id))
                    logout_user()
                    session.clear()
                    return redirect(url_for("login"))
                record_failure(store, keys)
                flash("Passwort konnte nicht geändert werden.", "error")
            elif action == "start" and not row["totp_secret"]:
                secret = pyotp.random_base32()
                with store.connect() as connection:
                    connection.execute("UPDATE users SET totp_pending = ? WHERE id = ?", (cipher.encrypt(secret.encode()).decode(), current_user.id))
                return redirect(url_for("account"))
            if action == "confirm" and row["totp_pending"] and not row["totp_secret"]:
                secret = cipher.decrypt(row["totp_pending"].encode()).decode()
                keys = subjects(current_user.username)
                totp = pyotp.TOTP(secret)
                current_step = int(time.time()) // totp.interval
                code = request.form.get("code", "")
                matched_step = next((step for step in (current_step - 1, current_step, current_step + 1)
                                     if secrets.compare_digest(totp.at(step * totp.interval), code)), None)
                if not rate_limited(store, keys) and matched_step is not None:
                    codes = [secrets.token_hex(5).upper() for _ in range(8)]
                    with store.connect() as connection:
                        connection.execute("UPDATE users SET totp_secret = totp_pending, totp_pending = NULL, last_totp_step = ? WHERE id = ?", (matched_step, current_user.id))
                        connection.executemany("INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)", [(current_user.id, recovery_hash(code)) for code in codes])
                    return render_template("recovery.html", codes=codes)
                record_failure(store, keys)
                flash("Der Code ist ungültig.", "error")
            elif action == "disable" and row["totp_secret"]:
                keys = subjects(current_user.username)
                if not rate_limited(store, keys) and password_valid(request.form.get("password", ""), row["password_hash"]) and verify_second_factor(current_user.id, request.form.get("code", "")):
                    with store.connect() as connection:
                        connection.execute("UPDATE users SET totp_secret = NULL, totp_pending = NULL, epoch = epoch + 1 WHERE id = ?", (current_user.id,))
                        connection.execute("DELETE FROM recovery_codes WHERE user_id = ?", (current_user.id,))
                    logout_user()
                    session.clear()
                    return redirect(url_for("login"))
                record_failure(store, keys)
                flash("Bestätigung fehlgeschlagen.", "error")
            return redirect(url_for("account"))
        with store.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (current_user.id,)).fetchone()
        qr = None
        secret = None
        if row["totp_pending"] and not row["totp_secret"]:
            secret = cipher.decrypt(row["totp_pending"].encode()).decode()
            image = qrcode.make(pyotp.TOTP(secret).provisioning_uri(name=current_user.username, issuer_name="Einkaufsliste"))
            output = io.BytesIO()
            image.save(output, format="PNG")
            qr = base64.b64encode(output.getvalue()).decode()
        return render_template("account.html", qr=qr, secret=secret)

    @app.route("/users", methods=["GET", "POST"])
    @admin_required
    def users():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "user")
            if not 1 <= len(username) <= 80 or len(password) < 12 or role not in ("admin", "user"):
                flash("Bitte gültigen Namen und ein Passwort mit mindestens 12 Zeichen eingeben.", "error")
            else:
                try:
                    with store.connect() as connection:
                        connection.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (username, hasher.hash(password), role))
                    flash("Benutzer angelegt.", "success")
                except sqlite3.IntegrityError:
                    flash("Dieser Benutzername ist bereits vergeben.", "error")
            return redirect(url_for("users"))
        with store.connect() as connection:
            entries = [dict(row) for row in connection.execute("SELECT id, username, role, active, totp_secret IS NOT NULL AS totp_enabled FROM users ORDER BY username")]
        return render_template("users.html", users=entries)

    @app.post("/users/<int:user_id>/<action>")
    @admin_required
    def manage_user(user_id, action):
        if action not in ("toggle", "reset-2fa", "password", "role", "delete"):
            abort(404)
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                abort(404)
            admins = connection.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]
            if user["role"] == "admin" and user["active"] and admins == 1 and action in ("toggle", "role", "delete"):
                flash("Der letzte aktive Administrator kann nicht entfernt werden.", "error")
            elif action == "toggle":
                connection.execute("UPDATE users SET active = 1 - active, epoch = epoch + 1 WHERE id = ?", (user_id,))
            elif action == "role":
                connection.execute("UPDATE users SET role = CASE role WHEN 'admin' THEN 'user' ELSE 'admin' END, epoch = epoch + 1 WHERE id = ?", (user_id,))
            elif action == "reset-2fa":
                connection.execute("UPDATE users SET totp_secret = NULL, totp_pending = NULL, last_totp_step = -1, epoch = epoch + 1 WHERE id = ?", (user_id,))
                connection.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
            elif action == "delete":
                connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
            else:
                password = request.form.get("password", "")
                if len(password) < 12:
                    flash("Das Passwort muss mindestens 12 Zeichen haben.", "error")
                else:
                    connection.execute("UPDATE users SET password_hash = ?, epoch = epoch + 1 WHERE id = ?", (hasher.hash(password), user_id))
        return redirect(url_for("users"))

    if app.config["ENABLE_SCHEDULER"]:
        runner.schedule(max(60, int(os.getenv("SYNC_INTERVAL_SECONDS", "300"))))
    return app