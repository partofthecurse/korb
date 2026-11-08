import json
import os
from pathlib import Path

import aiohttp
import pyotp
from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.exceptions import CannotAuthenticate
from aioamazondevices.structures import AmazonListItemStatus, AmazonListType, AmazonSaveDataConfig

from .sync import RemoteItem


def setting(name, default=""):
    filename = os.getenv(f"{name}_FILE")
    if filename:
        return Path(filename).read_text(encoding="utf-8").strip()
    return os.getenv(name, default)


AMAZON_SETTINGS = {
    "amazon_email": "AMAZON_EMAIL",
    "amazon_password": "AMAZON_PASSWORD",
    "amazon_totp_secret": "AMAZON_TOTP_SECRET",
}
AMAZON_DOMAINS = (
    "amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de",
    "amazon.fr", "amazon.it", "amazon.es", "amazon.co.jp", "amazon.in",
    "amazon.com.au", "amazon.nl", "amazon.com.be", "amazon.se", "amazon.pl",
    "amazon.com.tr", "amazon.ae", "amazon.sa", "amazon.sg", "amazon.com.br",
)


def get_amazon_credentials(store, cipher):
    credentials = {}
    for key, environment_name in AMAZON_SETTINGS.items():
        encrypted = store.get_setting(key)
        if encrypted is None:
            credentials[key] = setting(environment_name)
        else:
            try:
                credentials[key] = cipher.decrypt(encrypted.encode()).decode()
            except Exception as error:
                raise RuntimeError("Stored Amazon credentials cannot be decrypted") from error
    return credentials


class AmazonRemote:
    def __init__(self, data_dir, store, cipher):
        self.data_dir = Path(data_dir)
        self.store = store
        self.cipher = cipher
        self.legacy_login_file = self.data_dir / "amazon-login.json"
        self.session = None
        self.api = None
        self.totp = None
        self.reauthenticated = False

    async def __aenter__(self):
        credentials = get_amazon_credentials(self.store, self.cipher)
        email = credentials["amazon_email"]
        password = credentials["amazon_password"]
        totp = credentials["amazon_totp_secret"]
        if not email or not password or not totp:
            raise RuntimeError("Amazon credentials are not configured")
        self.totp = pyotp.TOTP("".join(totp.split()))
        login_data = self._load_login_data()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        try:
            self.api = AmazonEchoApi(
                self.session, email, password,
                save_data=AmazonSaveDataConfig(self.data_dir), login_data=login_data,
            )
            if login_data:
                try:
                    await self.api.login.login_mode_stored_data()
                except CannotAuthenticate:
                    await self.api.login.login_mode_interactive(self.totp.now())
                    self.reauthenticated = True
            else:
                domain = self.store.get_setting("amazon_domain") or setting("AMAZON_DOMAIN", "amazon.com")
                site = domain if domain.startswith("https://") else f"https://www.{domain}"
                self.api._session_state_data.country_specific_data(site)
                await self.api.login.login_mode_interactive(self.totp.now())
                self.reauthenticated = True
            self._save_login()
            return self
        except BaseException:
            await self.session.close()
            raise

    async def __aexit__(self, *_):
        try:
            self._save_login()
        finally:
            await self.session.close()

    def _save_login(self):
        data = self.api._session_state_data.login_stored_data
        if not data:
            return
        self._store_login_data(data)

    def _store_login_data(self, data):
        serialized = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        encrypted = self.cipher.encrypt(serialized.encode()).decode()
        self.store.set_settings({"amazon_login": encrypted})
        self.legacy_login_file.unlink(missing_ok=True)

    def _load_login_data(self):
        encrypted = self.store.get_setting("amazon_login")
        if encrypted:
            try:
                serialized = self.cipher.decrypt(encrypted.encode()).decode()
                return json.loads(serialized)
            except Exception as error:
                raise RuntimeError("Stored Amazon session cannot be decrypted") from error
        try:
            data = json.loads(self.legacy_login_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        self._store_login_data(data)
        return data

    async def read(self):
        try:
            await self.api._todo_handler.update_lists()
        except CannotAuthenticate:
            if self.reauthenticated:
                raise
            await self.api.login.login_mode_interactive(self.totp.now())
            self.reauthenticated = True
            self._save_login()
            await self.api._todo_handler.update_lists()
        shopping_lists = [item for item in self.api.todo_lists if item.list_type == AmazonListType.SHOP]
        if len(shopping_lists) != 1:
            raise RuntimeError("Expected exactly one Amazon shopping list")
        list_id = shopping_lists[0].id
        items = await self.api.get_todo_list_items(list_id)
        active = [RemoteItem(item.id, item.name, item.version) for item in items.values()
                  if item.status == AmazonListItemStatus.ACTIVE]
        return list_id, active, set(items), len(items) < 100

    async def delete(self, list_id, item):
        await self.api.delete_todo_list_item(list_id, item.id, item.version)