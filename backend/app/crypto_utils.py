"""Canonical hashing + Merkle tree utilities (RFC6962-style, deterministic).

Why hand-rolled: the audit chain must be reproducible by any auditor from the
raw PostgreSQL rows using nothing but SHA-256. No library version can change
the result.
"""
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

GENESIS_HASH = "0" * 64


def _default(obj: Any):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 safe."""
    if value is None:
        return "null"
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_default,
                      ensure_ascii=True)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def compute_row_hash(
    *,
    prev_hash: str,
    user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    old_value: Any,
    new_value: Any,
    timestamp: datetime,
) -> str:
    """SHA-256 hash chained over the previous row hash.

    Payload layout is fixed and documented so an external auditor can
    reproduce it byte-for-byte.
    """
    payload = "|".join(
        [
            prev_hash or GENESIS_HASH,
            str(user_id or ""),
            action or "",
            entity_type or "",
            str(entity_id or ""),
            canonical_json(old_value),
            canonical_json(new_value),
            timestamp.isoformat(),
        ]
    )
    return sha256_hex(payload)


# --------------------------------------------------------------- Merkle tree
def _pair_hash(left: str, right: str) -> str:
    return sha256_hex(bytes.fromhex(left) + bytes.fromhex(right))


def merkle_root(leaves: Iterable[str]) -> str:
    """Compute the Merkle root over hex leaf hashes.

    Leaves are already SHA-256 hex digests (audit row_hash values).
    Odd nodes are duplicated (Bitcoin-style) for a stable, simple algorithm.
    Returns a 0x-prefixed 32-byte hex string (bytes32, chain ready).
    """
    level = [h.lower().removeprefix("0x") for h in leaves]
    if not level:
        return "0x" + GENESIS_HASH
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_pair_hash(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return "0x" + level[0]


def merkle_proof(leaves: list[str], index: int) -> list[dict]:
    """Inclusion proof for leaf at `index`: list of {position, hash}."""
    level = [h.lower().removeprefix("0x") for h in leaves]
    if index < 0 or index >= len(level):
        raise IndexError("leaf index out of range")
    proof: list[dict] = []
    idx = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sibling = idx + 1 if idx % 2 == 0 else idx - 1
        proof.append({"position": "right" if idx % 2 == 0 else "left",
                      "hash": "0x" + level[sibling]})
        level = [_pair_hash(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        idx //= 2
    return proof


def verify_merkle_proof(leaf: str, proof: list[dict], root: str) -> bool:
    cur = leaf.lower().removeprefix("0x")
    for step in proof:
        sib = step["hash"].lower().removeprefix("0x")
        cur = _pair_hash(cur, sib) if step["position"] == "right" else _pair_hash(sib, cur)
    return ("0x" + cur) == root.lower()
