"""
CGAE Audit Storage — IPFS via Pinata

Stores CGAE audit certificates on IPFS through Pinata's free tier
(1 GB, 500 files, $0/month, no credit card).

Setup:
    1. Sign up at https://app.pinata.cloud/auth/sign-up
    2. Create API key at https://app.pinata.cloud/developers/api-keys
    3. export PINATA_JWT=<your_jwt>

Retrieval:  https://gateway.pinata.cloud/ipfs/{cid}

The CID is stored on-chain in the CGAE Anchor program's certify_agent
instruction so anyone can independently verify the audit certificate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

PINATA_API_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"
IPFS_GATEWAY = "https://gateway.pinata.cloud/ipfs"


@dataclass
class StoreResult:
    """Result of an audit store operation."""
    cid: str
    real: bool
    model_name: str
    file_path: str
    size_bytes: int = 0
    network: str = "solana-devnet"
    tx_hash: Optional[str] = None
    error: Optional[str] = None

    @property
    def explorer_url(self) -> Optional[str]:
        if self.real:
            return f"{IPFS_GATEWAY}/{self.cid}"
        return None

    def to_dict(self) -> dict:
        return {
            "cid": self.cid, "real": self.real,
            "model_name": self.model_name, "file_path": self.file_path,
            "size_bytes": self.size_bytes, "network": self.network,
            "tx_hash": self.tx_hash, "error": self.error,
            "explorer_url": self.explorer_url,
        }


class SolanaStore:
    """
    Stores audit JSON on IPFS via Pinata (free tier: 1 GB / 500 files).

    Falls back to a deterministic SHA-256 pseudo-CID when no JWT is set.
    """

    def __init__(self, fallback_ok: bool = True, **_kwargs):
        self._jwt = os.getenv("PINATA_JWT")
        self.fallback_ok = fallback_ok

    def store_audit_result(self, model_name: str, json_path: str | Path) -> StoreResult:
        json_path = Path(json_path)
        if not json_path.exists():
            raise FileNotFoundError(f"Audit file not found: {json_path}")

        if self._jwt:
            try:
                return self._upload(model_name, json_path)
            except Exception as e:
                msg = str(e)
                logger.warning(f"  [storage] Pinata upload failed for {model_name}: {msg}")
                if not self.fallback_ok:
                    raise
                return self._fallback(model_name, json_path, error=msg)

        reason = "no PINATA_JWT configured"
        logger.debug(f"  [storage] Upload unavailable ({reason}). Using deterministic CID for {model_name}.")
        return self._fallback(model_name, json_path, error=reason)

    def store_bytes(self, model_name: str, data: bytes, filename: str,
                    cache_dir: Optional[Path] = None) -> StoreResult:
        import tempfile
        d = cache_dir or Path(tempfile.gettempdir())
        d.mkdir(parents=True, exist_ok=True)
        p = d / filename
        p.write_bytes(data)
        return self.store_audit_result(model_name, p)

    def _upload(self, model_name: str, json_path: Path) -> StoreResult:
        content = json_path.read_bytes()
        boundary = "----CGAEBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{json_path.name}"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            PINATA_API_URL, data=body,
            headers={
                "Authorization": f"Bearer {self._jwt}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

        cid = data["IpfsHash"]
        logger.info(f"  [storage] Pinned {json_path.name} → IPFS {cid}")
        return StoreResult(cid=cid, real=True, model_name=model_name,
                           file_path=str(json_path), size_bytes=len(content))

    @staticmethod
    def _fallback(model_name: str, json_path: Path, error: Optional[str] = None) -> StoreResult:
        content = json_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        return StoreResult(
            cid=f"cgae_audit_{digest[:50]}", real=False, model_name=model_name,
            file_path=str(json_path), size_bytes=len(content),
            network="solana-devnet", error=error,
        )


_default_store: Optional[SolanaStore] = None


def get_store(**kwargs) -> SolanaStore:
    global _default_store
    if _default_store is None:
        _default_store = SolanaStore(**kwargs)
    return _default_store


def store_audit_json(model_name: str, json_path: str | Path) -> StoreResult:
    return SolanaStore().store_audit_result(model_name, json_path)


def check_setup() -> dict:
    has_jwt = bool(os.getenv("PINATA_JWT"))
    return {
        "ready": has_jwt,
        "pinata_configured": has_jwt,
        "network": "solana-devnet",
        "storage": "IPFS (via Pinata)",
        "instructions": (
            None if has_jwt else
            "To enable IPFS uploads:\n"
            "  1. Sign up at https://app.pinata.cloud/auth/sign-up (free, no credit card)\n"
            "  2. Create API key at https://app.pinata.cloud/developers/api-keys\n"
            "  3. export PINATA_JWT=<your_jwt>"
        ),
    }


if __name__ == "__main__":
    print(json.dumps(check_setup(), indent=2))
