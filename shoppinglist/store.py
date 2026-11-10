import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from .catalog import DEFAULT_FOODS


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity BETWEEN 1 AND 99),
                    completed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS imports (
                    list_id TEXT NOT NULL,
                    remote_id TEXT NOT NULL,
                    item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (list_id, remote_id)
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    active INTEGER NOT NULL DEFAULT 1,
                    epoch INTEGER NOT NULL DEFAULT 0,
                    totp_secret TEXT,
                    totp_pending TEXT,
                    last_totp_step INTEGER NOT NULL DEFAULT -1
                );
                CREATE TABLE IF NOT EXISTS recovery_codes (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    code_hash TEXT NOT NULL,
                    PRIMARY KEY (user_id, code_hash)
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    subject TEXT NOT NULL,
                    attempted_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS login_attempts_subject ON login_attempts(subject, attempted_at);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS foods (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    terms TEXT NOT NULL DEFAULT '',
                    icon TEXT NOT NULL,
                    category TEXT NOT NULL,
                    color TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
            if "quantity" not in columns:
                connection.execute("ALTER TABLE items ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity BETWEEN 1 AND 99)")
            if "name_key" not in columns:
                connection.execute("ALTER TABLE items ADD COLUMN name_key TEXT NOT NULL DEFAULT ''")
                rows = connection.execute("SELECT id, name FROM items ORDER BY id").fetchall()
                for row in rows:
                    connection.execute(
                        "UPDATE items SET name_key = ? WHERE id = ?",
                        (self.name_key(row["name"]), row["id"]),
                    )
                self._merge_duplicate_items(connection)
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS items_name_key ON items(name_key)")
            seeded = connection.execute("SELECT 1 FROM settings WHERE key = 'foods_seeded'").fetchone()
            if not seeded:
                self._insert_default_foods(connection)
                connection.execute("INSERT INTO settings (key, value) VALUES ('foods_seeded', '1')")

    @staticmethod
    def name_key(name):
        normalized = unicodedata.normalize("NFKC", " ".join(name.split()))
        return normalized.casefold()

    @staticmethod
    def _merge_duplicate_items(connection):
        duplicates = connection.execute(
            "SELECT name_key, MIN(id) AS keep_id, SUM(quantity) AS quantity, "
            "MIN(completed) AS completed FROM items GROUP BY name_key HAVING COUNT(*) > 1"
        ).fetchall()
        for duplicate in duplicates:
            ids = [row["id"] for row in connection.execute(
                "SELECT id FROM items WHERE name_key = ? ORDER BY id", (duplicate["name_key"],)
            )]
            connection.execute(
                "UPDATE imports SET item_id = ? WHERE item_id IN "
                "(SELECT id FROM items WHERE name_key = ? AND id != ?)",
                (duplicate["keep_id"], duplicate["name_key"], duplicate["keep_id"]),
            )
            connection.execute(
                "UPDATE items SET quantity = ?, completed = ? WHERE id = ?",
                (min(99, duplicate["quantity"]), duplicate["completed"], duplicate["keep_id"]),
            )
            connection.executemany("DELETE FROM items WHERE id = ?", [(item_id,) for item_id in ids[1:]])

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def import_item(self, list_id, remote_id, name):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT deleted FROM imports WHERE list_id = ? AND remote_id = ?",
                (list_id, remote_id),
            ).fetchone()
            if existing:
                return not existing["deleted"]
            key = self.name_key(name)
            item = connection.execute(
                "SELECT id, quantity FROM items WHERE name_key = ?", (key,)
            ).fetchone()
            if item:
                if item["quantity"] >= 99:
                    raise RuntimeError("Maximum quantity reached for an imported item")
                item_id = item["id"]
                connection.execute(
                    "UPDATE items SET quantity = quantity + 1, completed = 0 WHERE id = ?",
                    (item_id,),
                )
            else:
                item_id = connection.execute(
                    "INSERT INTO items (name, name_key) VALUES (?, ?)", (name, key)
                ).lastrowid
            connection.execute(
                "INSERT INTO imports (list_id, remote_id, item_id) VALUES (?, ?, ?)",
                (list_id, remote_id, item_id),
            )
            return True

    def mark_deleted(self, list_id, remote_id):
        with self.connect() as connection:
            connection.execute(
                "UPDATE imports SET deleted = 1 WHERE list_id = ? AND remote_id = ?",
                (list_id, remote_id),
            )

    def list_items(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, name, quantity, completed, created_at FROM items ORDER BY completed, id DESC"
            )]

    def add_item(self, name):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            key = self.name_key(name)
            existing = connection.execute("SELECT id, quantity FROM items WHERE name_key = ?", (key,)).fetchone()
            if existing:
                if existing["quantity"] >= 99:
                    return existing["id"], 99, False, False
                quantity = existing["quantity"] + 1
                connection.execute(
                    "UPDATE items SET quantity = ?, completed = 0 WHERE id = ?",
                    (quantity, existing["id"]),
                )
                return existing["id"], quantity, False, True
            item_id = connection.execute(
                "INSERT INTO items (name, name_key) VALUES (?, ?)", (name, key)
            ).lastrowid
            return item_id, 1, True, True

    def change_quantity(self, item_id, delta):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            item = connection.execute("SELECT quantity FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                return None
            quantity = item["quantity"] + delta
            if not 1 <= quantity <= 99:
                return item["quantity"]
            connection.execute(
                "UPDATE items SET quantity = ?, completed = CASE WHEN ? > 0 THEN 0 ELSE completed END WHERE id = ?",
                (quantity, delta, item_id),
            )
            return quantity

    def complete_item(self, item_id, completed):
        with self.connect() as connection:
            return connection.execute(
                "UPDATE items SET completed = ? WHERE id = ?", (int(completed), item_id)
            ).rowcount

    def delete_item(self, item_id):
        with self.connect() as connection:
            return connection.execute("DELETE FROM items WHERE id = ?", (item_id,)).rowcount

    def get_setting(self, key):
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_settings(self, settings):
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                list(settings.items()),
            )

    def delete_settings(self, keys):
        with self.connect() as connection:
            connection.executemany("DELETE FROM settings WHERE key = ?", [(key,) for key in keys])
    @classmethod
    def _insert_default_foods(cls, connection):
        return sum(connection.execute(
            "INSERT OR IGNORE INTO foods (name, name_key, terms, icon, category, color) VALUES (?, ?, ?, ?, ?, ?)",
            (name, cls.name_key(name), terms, icon, category, color),
        ).rowcount for name, terms, icon, category, color in DEFAULT_FOODS)

    def restore_default_foods(self):
        with self.connect() as connection:
            return self._insert_default_foods(connection)

    def list_foods(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, name, terms, icon, category, color FROM foods ORDER BY name COLLATE NOCASE"
            )]

    def save_food(self, food_id, name, terms, icon, category, color):
        """Insert (food_id None) or update a food entry. Returns False on a duplicate name."""
        values = (name, self.name_key(name), terms, icon, category, color)
        try:
            with self.connect() as connection:
                if food_id is None:
                    connection.execute(
                        "INSERT INTO foods (name, name_key, terms, icon, category, color) VALUES (?, ?, ?, ?, ?, ?)",
                        values,
                    )
                    return True
                return bool(connection.execute(
                    "UPDATE foods SET name = ?, name_key = ?, terms = ?, icon = ?, category = ?, color = ? WHERE id = ?",
                    (*values, food_id),
                ).rowcount)
        except sqlite3.IntegrityError:
            return False

    def delete_food(self, food_id):
        with self.connect() as connection:
            return connection.execute("DELETE FROM foods WHERE id = ?", (food_id,)).rowcount
