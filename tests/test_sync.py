import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from shoppinglist.store import Store
from shoppinglist.sync import RemoteItem, Synchronizer


class Remote:
    def __init__(self, items, store):
        self.items = {item.id: item for item in items}
        self.store = store
        self.fail = False

    async def read(self):
        return "shopping", list(self.items.values()), set(self.items), len(self.items) < 100

    async def delete(self, list_id, item):
        with self.store.connect() as connection:
            assert connection.execute(
                "SELECT 1 FROM imports WHERE list_id = ? AND remote_id = ?",
                (list_id, item.id),
            ).fetchone()
        if self.fail:
            raise OSError("network failure")
        self.items.pop(item.id)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "shopping.db")
        self.item = RemoteItem("one", "Milk", 1)
        self.remote = Remote([self.item], self.store)
        self.sync = Synchronizer(self.store, self.remote)

    def test_import_commits_before_delete(self):
        asyncio.run(self.sync.run())
        self.assertEqual(self.store.list_items()[0]["name"], "Milk")
        self.assertEqual(self.store.list_items()[0]["quantity"], 1)
        self.assertFalse(self.remote.items)
        self.assertFalse(self.store.import_item("shopping", "one", "Milk"))

    def test_same_item_in_alexa_list_increments_quantity_once(self):
        duplicate = RemoteItem("two", " milk ", 1)
        self.remote.items[duplicate.id] = duplicate
        asyncio.run(self.sync.run())
        items = self.store.list_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], 2)
        self.assertFalse(self.remote.items)
        self.assertFalse(self.store.import_item("shopping", "two", " milk "))

    def test_local_duplicate_increments_existing_item(self):
        first_id, first_quantity, created, _ = self.store.add_item("Apfel")
        second_id, second_quantity, created_again, _ = self.store.add_item(" apfel ")
        self.assertEqual(first_id, second_id)
        self.assertEqual((first_quantity, second_quantity), (1, 2))
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(len(self.store.list_items()), 1)

    def test_legacy_duplicate_items_merge_during_schema_migration(self):
        legacy_path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript("""
                CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE imports (list_id TEXT NOT NULL, remote_id TEXT NOT NULL, item_id INTEGER REFERENCES items(id) ON DELETE SET NULL, deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (list_id, remote_id));
            """)
            connection.execute("INSERT INTO items (name) VALUES ('Milk')")
            connection.execute("INSERT INTO items (name) VALUES (' milk ')")
            connection.execute("INSERT INTO imports (list_id, remote_id, item_id) VALUES ('shopping', 'one', 1)")
            connection.execute("INSERT INTO imports (list_id, remote_id, item_id) VALUES ('shopping', 'two', 2)")
        migrated = Store(legacy_path)
        items = migrated.list_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], 2)
        with migrated.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(DISTINCT item_id) FROM imports").fetchone()[0], 1)

    def test_retry_does_not_duplicate(self):
        self.remote.fail = True
        with self.assertRaises(RuntimeError):
            asyncio.run(self.sync.run())
        self.remote.fail = False
        asyncio.run(self.sync.run())
        self.assertEqual(len(self.store.list_items()), 1)
        self.assertFalse(self.remote.items)

    def test_deleted_local_item_is_not_recreated(self):
        self.remote.fail = True
        with self.assertRaises(RuntimeError):
            asyncio.run(self.sync.run())
        with self.store.connect() as connection:
            connection.execute("DELETE FROM items")
        self.remote.fail = False
        asyncio.run(self.sync.run())
        self.assertEqual(self.store.list_items(), [])

    def test_unconfirmed_deletion_remains_pending(self):
        async def pretend_delete(list_id, item):
            return None
        self.remote.delete = pretend_delete
        asyncio.run(self.sync.run())
        self.assertTrue(self.store.import_item("shopping", "one", "Milk"))

    def test_new_remote_item_is_not_deleted_in_current_import(self):
        new_item = RemoteItem("new", "Bread", 1)
        async def delete_with_new_item(list_id, item):
            self.remote.items.pop(item.id)
            self.remote.items[new_item.id] = new_item
        self.remote.delete = delete_with_new_item
        asyncio.run(self.sync.run())
        self.assertEqual(list(self.remote.items), ["new"])
        self.assertEqual(len(self.store.list_items()), 1)

    def test_truncated_result_does_not_confirm_deletion(self):
        self.remote.items.update({str(index): RemoteItem(str(index), "Item", 1) for index in range(100)})
        async def delete_only_first(list_id, item):
            if item.id == "one":
                self.remote.items.pop(item.id)
        self.remote.delete = delete_only_first
        with self.assertRaises(RuntimeError):
            asyncio.run(self.sync.run())
        self.assertTrue(self.store.import_item("shopping", "one", "Milk"))


if __name__ == "__main__":
    unittest.main()