import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteItem:
    id: str
    name: str
    version: int


class Synchronizer:
    def __init__(self, store, remote):
        self.store = store
        self.remote = remote
        self.lock = asyncio.Lock()

    async def run(self):
        async with self.lock:
            list_id, items, _, _ = await self.remote.read()
            pending = []
            for item in items:
                if self.store.import_item(list_id, item.id, item.name):
                    pending.append(item)
            errors = []
            for item in pending:
                try:
                    await self.remote.delete(list_id, item)
                except Exception:
                    errors.append(item.id)
            confirmed_list_id, _, observed_ids, complete = await self.remote.read()
            if confirmed_list_id != list_id:
                raise RuntimeError("The Amazon shopping list changed during synchronization")
            if complete:
                for item in pending:
                    if item.id not in observed_ids:
                        self.store.mark_deleted(list_id, item.id)
            if errors:
                raise RuntimeError(f"Failed to delete {len(errors)} Amazon item(s)")
            if not complete:
                raise RuntimeError("The Amazon shopping list response is incomplete")
            return len(pending)