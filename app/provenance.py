"""Provenance records and slugs.

A venture's provenance record is a SHA-256 hash over the raw idea, the exact
minting timestamp, and the creator's token. It proves *this text existed at
this moment and was minted by this creator*. It is not a patent, a trademark,
or a copyright registration, and the UI never claims that it is.

Optional anchoring to Bitcoin via OpenTimestamps is described in the README;
the hash produced here is exactly what you would anchor.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from datetime import datetime, timezone

_ADJ = [
    "quiet", "brass", "north", "amber", "iron", "cedar", "salt", "ember",
    "flint", "moss", "slate", "wren", "ridge", "pale", "ash", "river",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def mint(idea: str, created_at: str, owner_token: str) -> dict:
    """Return {"hash": <64 hex>, "code": "MNF-XXXX-XXXX-XXXX", "minted_at": iso}."""
    material = f"{idea.strip()}\n{created_at}\n{owner_token}".encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    short = digest[:12].upper()
    code = f"MNF-{short[0:4]}-{short[4:8]}-{short[8:12]}"
    return {"hash": digest, "code": code, "minted_at": created_at}


def slugify(name: str) -> str:
    base = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    base = base[:40].strip("-") or "venture"
    return base


def unique_slug(name: str, exists) -> str:
    """exists(slug) -> bool. Appends a short suffix on collision."""
    base = slugify(name)
    if not exists(base):
        return base
    for _ in range(20):
        candidate = f"{base}-{secrets.token_hex(2)}"
        if not exists(candidate):
            return candidate
    return f"{base}-{secrets.token_hex(4)}"


def new_token() -> str:
    return secrets.token_urlsafe(24)
