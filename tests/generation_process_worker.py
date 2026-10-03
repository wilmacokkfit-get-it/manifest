"""Spawn-safe SQLite worker: no server import or real service connections."""
import asyncio

from app.store import SQLiteStore


def reserve_from_process(directory, token, caller, hour, day, global_cap):
    store = SQLiteStore(directory)
    try:
        return asyncio.run(store.reserve_generation(token, caller, hour, day, global_cap))
    finally:
        store._db.close()