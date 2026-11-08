import getpass
import os
import sys
from pathlib import Path

from .security import hasher
from .store import Store


def main():
    os.umask(0o077)
    arguments = sys.argv[1:]
    if arguments != ["create-admin"] and not (len(arguments) == 2 and arguments[0] in ("reset-2fa", "reset-password")):
        print("Usage: python -m shoppinglist.manage create-admin | reset-2fa USER | reset-password USER")
        return 2
    store = Store(Path(os.getenv("SHOPPING_DATA_DIR", "/data")) / "shopping.db")
    if arguments[0] != "create-admin":
        username = arguments[1]
        with store.connect() as connection:
            user = connection.execute("SELECT id FROM users WHERE username = ? AND role = 'admin' AND active = 1", (username,)).fetchone()
        if not user:
            print("Active administrator not found.")
            return 1
        if arguments[0] == "reset-password":
            password = getpass.getpass("New admin password (at least 12 characters): ")
            confirmation = getpass.getpass("Repeat password: ")
            if len(password) < 12 or password != confirmation:
                print("Invalid password.")
                return 1
            with store.connect() as connection:
                connection.execute("UPDATE users SET password_hash = ?, epoch = epoch + 1 WHERE id = ?", (hasher.hash(password), user["id"]))
        else:
            with store.connect() as connection:
                connection.execute("UPDATE users SET totp_secret = NULL, totp_pending = NULL, last_totp_step = -1, epoch = epoch + 1 WHERE id = ?", (user["id"],))
                connection.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user["id"],))
        print("Administrator account updated.")
        return 0
    with store.connect() as connection:
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            print("A user already exists; use the web administration.")
            return 1
    username = input("Admin username: ").strip()
    password = getpass.getpass("Admin password (at least 12 characters): ")
    confirmation = getpass.getpass("Repeat password: ")
    if not username or len(username) > 80 or len(password) < 12 or password != confirmation:
        print("Invalid username or password.")
        return 1
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            print("A user already exists.")
            return 1
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (username, hasher.hash(password)),
        )
    print("Admin account created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())