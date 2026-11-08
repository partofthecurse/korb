import hashlib
import os
import secrets
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet


hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("unusable-placeholder-password")


def password_valid(password, password_hash):
    try:
        return hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def secret_file(path, generator):
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_bytes()
    value = generator()
    with os.fdopen(descriptor, "wb") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())
    return value


def load_keys(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    signing_key = secret_file(directory / "session.key", lambda: secrets.token_bytes(32))
    encryption_key = secret_file(directory / "totp.key", Fernet.generate_key)
    return signing_key, Fernet(encryption_key)


def load_amazon_cipher(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Fernet(secret_file(directory / "amazon.key", Fernet.generate_key))


def recovery_hash(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def rate_limited(store, subjects):
    now = int(time.time())
    with store.connect() as connection:
        connection.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - 900,))
        return any(connection.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE subject = ? AND attempted_at >= ?",
            (subject, now - 900),
        ).fetchone()[0] >= (50 if subject.startswith("ip:") else 5) for subject in subjects)


def record_failure(store, subjects):
    now = int(time.time())
    with store.connect() as connection:
        connection.executemany(
            "INSERT INTO login_attempts (subject, attempted_at) VALUES (?, ?)",
            [(subject, now) for subject in subjects],
        )


def clear_failures(store, subjects):
    with store.connect() as connection:
        connection.executemany("DELETE FROM login_attempts WHERE subject = ?", [(subject,) for subject in subjects if subject.startswith("user:")])