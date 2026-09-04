"""Pluggable Merkle-root anchoring.

ANCHOR_MODE=LOCAL         -> root stored in PostgreSQL only (default, zero setup)
ANCHOR_MODE=POLYGON_AMOY  -> root committed on-chain via web3.py

Both modes share the exact same Merkle computation, so switching the mode never
changes the cryptographic result - only where the root is published.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import settings
from .crypto_utils import merkle_proof, merkle_root, verify_merkle_proof
from .models import AuditAnchor, AuditTrail

logger = logging.getLogger(__name__)

# Minimal ABI for the AuditAnchor contract described in the project brief:
#   function commitRoot(bytes32 merkleRoot) external
#   function getRoot(uint256 index) external view returns (bytes32)
#   function rootCount() external view returns (uint256)
ANCHOR_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "merkleRoot", "type": "bytes32"}],
        "name": "commitRoot",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "index", "type": "uint256"}],
        "name": "getRoot",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "rootCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

AMOY_EXPLORER = "https://amoy.polygonscan.com/tx/"


class AnchorError(Exception):
    pass


# ----------------------------------------------------------------- chain layer
def polygon_available() -> tuple[bool, str]:
    if settings.ANCHOR_MODE != "POLYGON_AMOY":
        return False, "ANCHOR_MODE is not POLYGON_AMOY"
    missing = [
        k
        for k, v in {
            "POLYGON_RPC_URL": settings.POLYGON_RPC_URL,
            "POLYGON_PRIVATE_KEY": settings.POLYGON_PRIVATE_KEY,
            "POLYGON_CONTRACT_ADDRESS": settings.POLYGON_CONTRACT_ADDRESS,
        }.items()
        if not v
    ]
    if missing:
        return False, f"Missing env vars: {', '.join(missing)}"
    return True, "ok"


def _commit_on_chain(root_hex: str) -> dict:
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(settings.POLYGON_RPC_URL, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise AnchorError("Cannot connect to Polygon Amoy RPC")

    acct = w3.eth.account.from_key(settings.POLYGON_PRIVATE_KEY)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.POLYGON_CONTRACT_ADDRESS), abi=ANCHOR_ABI
    )
    tx = contract.functions.commitRoot(bytes.fromhex(root_hex.removeprefix("0x"))).build_transaction(
        {
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "chainId": settings.POLYGON_CHAIN_ID,
            "gas": 200000,
            "maxFeePerGas": w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": w3.to_wei(30, "gwei"),
        }
    )
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    return {
        "chain": "polygon-amoy",
        "tx_hash": receipt["transactionHash"].hex(),
        "block_number": receipt["blockNumber"],
        "explorer_url": AMOY_EXPLORER + receipt["transactionHash"].hex(),
    }


def _read_on_chain_root(index: int) -> str | None:
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(settings.POLYGON_RPC_URL, request_kwargs={"timeout": 30}))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.POLYGON_CONTRACT_ADDRESS), abi=ANCHOR_ABI
    )
    value = contract.functions.getRoot(index).call()
    return "0x" + value.hex().removeprefix("0x")


# ----------------------------------------------------------------- public API
def leaf_hash(row: AuditTrail) -> str:
    """Merkle leaf = row hash RECOMPUTED from the row's live content.

    Critical design point: the leaf is never taken from the stored `row_hash`
    column. If a DBA rewrote any field (even with the immutability triggers
    disabled), the recomputed leaf changes, therefore the recomputed Merkle
    root no longer equals the anchored root -> tampering is exposed.
    """
    from .crypto_utils import compute_row_hash

    return compute_row_hash(
        prev_hash=row.prev_hash,
        user_id=row.user_id,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        old_value=row.old_value,
        new_value=row.new_value,
        timestamp=row.timestamp,
    )


def pending_rows(db: Session) -> list[AuditTrail]:
    last = db.execute(
        text("SELECT COALESCE(MAX(batch_end_id), 0) FROM audit_anchors")
    ).scalar_one()
    return (
        db.execute(select(AuditTrail).where(AuditTrail.id > last).order_by(AuditTrail.id))
        .scalars()
        .all()
    )


def commit_batch(db: Session, force: bool = False) -> dict:
    """Batch un-anchored audit rows, compute the Merkle root and anchor it."""
    rows = pending_rows(db)
    if not rows:
        return {"anchored": False, "reason": "No un-anchored audit rows", "anchor": None}
    if not force and len(rows) < settings.ANCHOR_BATCH_SIZE:
        return {
            "anchored": False,
            "reason": (
                f"Only {len(rows)} pending rows; batch size is "
                f"{settings.ANCHOR_BATCH_SIZE}. Use force=true to anchor now."
            ),
            "pending_rows": len(rows),
            "anchor": None,
        }

    leaves = [leaf_hash(r) for r in rows]
    root = merkle_root(leaves)

    chain_info = {"chain": "local", "tx_hash": None, "block_number": None, "explorer_url": None}
    note = "LOCAL anchor mode - Merkle root stored in PostgreSQL (append-only table)."
    ok, why = polygon_available()
    if settings.ANCHOR_MODE == "POLYGON_AMOY":
        if not ok:
            note = f"POLYGON_AMOY requested but unavailable ({why}). Fell back to LOCAL anchor."
        else:
            try:
                chain_info = _commit_on_chain(root)
                note = "Merkle root committed to Polygon Amoy testnet."
            except Exception as exc:  # noqa: BLE001
                logger.exception("On-chain anchoring failed")
                note = f"On-chain anchoring failed ({exc}). Fell back to LOCAL anchor."

    anchor = AuditAnchor(
        batch_start_id=rows[0].id,
        batch_end_id=rows[-1].id,
        row_count=len(rows),
        merkle_root=root,
        chain=chain_info["chain"],
        tx_hash=chain_info["tx_hash"],
        block_number=chain_info["block_number"],
        explorer_url=chain_info["explorer_url"],
        committed_at=datetime.now(timezone.utc),
        verification_status="matched",
        notes=note,
    )
    db.add(anchor)
    db.commit()
    db.refresh(anchor)
    return {"anchored": True, "reason": note, "anchor": anchor_to_dict(anchor)}


def anchor_to_dict(a: AuditAnchor) -> dict:
    return {
        "id": a.id,
        "batch_start_id": a.batch_start_id,
        "batch_end_id": a.batch_end_id,
        "row_count": a.row_count,
        "merkle_root": a.merkle_root,
        "chain": a.chain,
        "tx_hash": a.tx_hash,
        "block_number": a.block_number,
        "explorer_url": a.explorer_url,
        "committed_at": a.committed_at.isoformat() if a.committed_at else None,
        "verification_status": a.verification_status,
        "notes": a.notes,
    }


def verify_anchors(db: Session) -> dict:
    """Recompute every anchored batch from live DB rows and compare roots.

    This is the tamper-evidence check: if any audit row was altered (even with
    triggers disabled by a DBA), its row_hash changes, the Merkle root changes
    and the batch is reported as MISMATCHED.
    """
    from .audit import verify_hash_chain

    anchors = db.execute(select(AuditAnchor).order_by(AuditAnchor.id)).scalars().all()
    results = []
    for a in anchors:
        rows = (
            db.execute(
                select(AuditTrail)
                .where(AuditTrail.id >= a.batch_start_id, AuditTrail.id <= a.batch_end_id)
                .order_by(AuditTrail.id)
            )
            .scalars()
            .all()
        )
        recomputed = merkle_root([leaf_hash(r) for r in rows])
        on_chain_root = None
        if a.chain == "polygon-amoy" and polygon_available()[0]:
            try:
                idx = sum(1 for x in anchors if x.id < a.id and x.chain == "polygon-amoy")
                on_chain_root = _read_on_chain_root(idx)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not read on-chain root: %s", exc)

        matched = recomputed == a.merkle_root and (
            on_chain_root is None or on_chain_root.lower() == a.merkle_root.lower()
        )
        results.append(
            {
                "anchor_id": a.id,
                "chain": a.chain,
                "tx_hash": a.tx_hash,
                "block_number": a.block_number,
                "explorer_url": a.explorer_url,
                "batch_start_id": a.batch_start_id,
                "batch_end_id": a.batch_end_id,
                "expected_row_count": a.row_count,
                "actual_row_count": len(rows),
                "anchored_merkle_root": a.merkle_root,
                "recomputed_merkle_root": recomputed,
                "on_chain_merkle_root": on_chain_root,
                "verification_status": "matched" if matched else "mismatched",
            }
        )

    chain_check = verify_hash_chain(db)
    all_matched = all(r["verification_status"] == "matched" for r in results)
    return {
        "anchor_mode": settings.ANCHOR_MODE,
        "anchors_checked": len(results),
        "pending_unanchored_rows": len(pending_rows(db)),
        "hash_chain": chain_check,
        "anchors": results,
        "overall_status": (
            "VERIFIED" if all_matched and chain_check["chain_intact"] else "TAMPER_DETECTED"
        ),
    }


def inclusion_proof(db: Session, audit_id: int) -> dict:
    """Merkle inclusion proof that a specific audit row is inside an anchor."""
    anchor = db.execute(
        select(AuditAnchor)
        .where(AuditAnchor.batch_start_id <= audit_id, AuditAnchor.batch_end_id >= audit_id)
        .order_by(AuditAnchor.id)
    ).scalars().first()
    if anchor is None:
        raise AnchorError(f"Audit row {audit_id} is not yet included in any anchored batch")
    rows = (
        db.execute(
            select(AuditTrail)
            .where(AuditTrail.id >= anchor.batch_start_id, AuditTrail.id <= anchor.batch_end_id)
            .order_by(AuditTrail.id)
        )
        .scalars()
        .all()
    )
    leaves = [leaf_hash(r) for r in rows]
    index = next(i for i, r in enumerate(rows) if r.id == audit_id)
    proof = merkle_proof(leaves, index)
    return {
        "audit_id": audit_id,
        "leaf_hash": "0x" + leaves[index],
        "leaf_index": index,
        "anchor": anchor_to_dict(anchor),
        "proof": proof,
        "proof_valid": verify_merkle_proof(leaves[index], proof, anchor.merkle_root),
    }
