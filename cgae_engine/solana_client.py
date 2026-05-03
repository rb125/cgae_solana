"""
CGAE On-Chain Client — Python bridge to the Anchor program on Solana Devnet.

Calls the deployed CGAE program for:
  - initialize (once)
  - register_agent (per agent)
  - certify_agent (after audit)
  - create_contract / accept_contract / complete_contract / fail_contract
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

from solana.rpc.api import Client as SolanaClient
from solana.rpc.commitment import Confirmed, Finalized
from solana.rpc.types import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.instruction import Instruction, AccountMeta
from solders.transaction import Transaction
from solders.message import Message
import os
import json
logger = logging.getLogger(__name__)

PROGRAM_ID = Pubkey.from_string("Aydqk82Wt1Cni6GQHTSJimtVskZ9PqvA6QyhtRjcRN3a")
RPC_URL = "https://api.devnet.solana.com"

def _load_keypair(path: str = None) -> Keypair:
    # 1. Try environment variable (HF / production)
    key_env = os.getenv("SOLANA_PRIVATE_KEY")
    if key_env:
        try:
            data = json.loads(key_env)
            return Keypair.from_bytes(bytes(data))
        except Exception as e:
            raise RuntimeError(f"Invalid SOLANA_PRIVATE_KEY: {e}")

    # 2. Optional explicit path override
    if path:
        p = Path(path)
        if p.exists():
            return Keypair.from_bytes(bytes(json.loads(p.read_text())))

    # 3. Local dev fallback (your current setup)
    default_path = Path.home() / ".config/solana/id.json"
    if default_path.exists():
        return Keypair.from_bytes(bytes(json.loads(default_path.read_text())))

    # 4. Fail clearly
    raise RuntimeError(
        "No Solana keypair found. Set SOLANA_PRIVATE_KEY or provide a valid path."
    )

def _discriminator(name: str) -> bytes:
    """Anchor 8-byte instruction discriminator: sha256("global:<name>")[:8]"""
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def _find_protocol_pda() -> tuple[Pubkey, int]:
    return Pubkey.find_program_address([b"protocol"], PROGRAM_ID)


def _find_agent_pda(owner: Pubkey) -> tuple[Pubkey, int]:
    return Pubkey.find_program_address([b"agent", bytes(owner)], PROGRAM_ID)


def _find_contract_pda(protocol_pda: Pubkey, contract_id: int) -> tuple[Pubkey, int]:
    return Pubkey.find_program_address(
        [b"contract", bytes(protocol_pda), contract_id.to_bytes(4, "little")],
        PROGRAM_ID,
    )


class CGAEOnChain:
    """Thin Python client for the CGAE Anchor program."""

    def __init__(self, keypair_path: str = None, rpc_url: str = RPC_URL):
        self.client = SolanaClient(rpc_url)
        self.admin = _load_keypair(keypair_path)
        self.protocol_pda, self._protocol_bump = _find_protocol_pda()
        self._contract_count = 0
        # Per-agent keypairs (generated deterministically for the demo)
        self._agent_keypairs: dict[str, Keypair] = {}

    def _send(self, ix: Instruction, signers: list[Keypair], label: str) -> Optional[str]:
        """Build, sign, send a transaction. Returns signature or None."""
        try:
            blockhash_resp = self.client.get_latest_blockhash(Finalized)
            blockhash = blockhash_resp.value.blockhash
            msg = Message.new_with_blockhash([ix], self.admin.pubkey(), blockhash)
            tx = Transaction.new_unsigned(msg)
            tx.sign(signers, blockhash)
            opts = TxOpts(skip_preflight=True, preflight_commitment=Confirmed)
            resp = self.client.send_transaction(tx, opts=opts)
            sig = str(resp.value)
            logger.info(f"  [on-chain] {label}: {sig}")
            time.sleep(1)
            return sig
        except Exception as e:
            logger.warning(f"  [on-chain] {label} failed: {e}")
            return None

    def initialize(self) -> Optional[str]:
        """Initialize the protocol state PDA (idempotent — skips if exists)."""
        acct = self.client.get_account_info(self.protocol_pda, Confirmed)
        if acct.value is not None:
            logger.info("  [on-chain] Protocol already initialized")
            # Read contract_count from account data
            data = bytes(acct.value.data)
            # offset: 8 (disc) + 32 (admin) + 38 (thresholds) + 48 (ceilings) = 126
            # contract_count is at offset 126+4 = 130 (after agent_count)
            if len(data) > 134:
                import struct
                self._contract_count = struct.unpack_from("<I", data, 130)[0]
            return None

        disc = _discriminator("initialize")
        ix = Instruction(
            PROGRAM_ID,
            disc,
            [
                AccountMeta(self.protocol_pda, is_signer=False, is_writable=True),
                AccountMeta(self.admin.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )
        return self._send(ix, [self.admin], "initialize")

    def get_or_create_agent_keypair(self, model_name: str) -> Keypair:
        """Get a deterministic keypair for an agent (demo only)."""
        if model_name not in self._agent_keypairs:
            seed = hashlib.sha256(f"cgae-agent-{model_name}".encode()).digest()
            self._agent_keypairs[model_name] = Keypair.from_seed(seed)
        return self._agent_keypairs[model_name]

    def fund_agent(self, agent_kp: Keypair, lamports: int = 10_000_000) -> Optional[str]:
        """Airdrop or transfer SOL to an agent wallet for rent + escrow."""
        balance = self.client.get_balance(agent_kp.pubkey(), Confirmed).value
        if balance >= lamports:
            return None
        # Transfer from admin
        from solders.system_program import transfer, TransferParams
        ix = transfer(TransferParams(
            from_pubkey=self.admin.pubkey(),
            to_pubkey=agent_kp.pubkey(),
            lamports=lamports - balance,
        ))
        return self._send(ix, [self.admin], f"fund {str(agent_kp.pubkey())[:8]}...")

    def register_agent(self, model_name: str) -> Optional[str]:
        """Register an agent on-chain. Returns tx signature."""
        agent_kp = self.get_or_create_agent_keypair(model_name)
        agent_pda, _ = _find_agent_pda(agent_kp.pubkey())

        # Check if already registered
        acct = self.client.get_account_info(agent_pda, Confirmed)
        if acct.value is not None:
            logger.info(f"  [on-chain] {model_name} already registered")
            return None

        # Fund agent wallet for rent
        self.fund_agent(agent_kp)

        arch_hash = hashlib.md5(model_name.encode()).digest()  # 16 bytes
        name_bytes = model_name.encode("utf-8")[:64]

        # Borsh: [u8;16] arch_hash + String model_name (4-byte len prefix + bytes)
        data = _discriminator("register_agent")
        data += arch_hash
        data += len(name_bytes).to_bytes(4, "little") + name_bytes

        ix = Instruction(
            PROGRAM_ID,
            data,
            [
                AccountMeta(agent_pda, is_signer=False, is_writable=True),
                AccountMeta(self.protocol_pda, is_signer=False, is_writable=True),
                AccountMeta(agent_kp.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )
        return self._send(ix, [self.admin, agent_kp], f"register {model_name}")

    def certify_agent(
        self, model_name: str, cc: float, er: float, as_: float, ih: float, audit_cid: str
    ) -> Optional[str]:
        """Certify agent with robustness scores on-chain."""
        agent_kp = self.get_or_create_agent_keypair(model_name)
        agent_pda, _ = _find_agent_pda(agent_kp.pubkey())

        # Scale floats to u16 (0-10000)
        cc_u16 = min(10000, int(cc * 10000))
        er_u16 = min(10000, int(er * 10000))
        as_u16 = min(10000, int(as_ * 10000))
        ih_u16 = min(10000, int(ih * 10000))
        cid_bytes = audit_cid.encode("utf-8")[:128]

        data = _discriminator("certify_agent")
        data += cc_u16.to_bytes(2, "little")
        data += er_u16.to_bytes(2, "little")
        data += as_u16.to_bytes(2, "little")
        data += ih_u16.to_bytes(2, "little")
        data += len(cid_bytes).to_bytes(4, "little") + cid_bytes

        ix = Instruction(
            PROGRAM_ID,
            data,
            [
                AccountMeta(agent_pda, is_signer=False, is_writable=True),
                AccountMeta(self.protocol_pda, is_signer=False, is_writable=False),
                AccountMeta(self.admin.pubkey(), is_signer=True, is_writable=False),
            ],
        )
        return self._send(ix, [self.admin], f"certify {model_name}")

    def create_contract(
        self, min_tier: int, reward_lamports: int, penalty_lamports: int,
        domain: str, objective_hash: bytes = None, constraints_hash: bytes = None,
    ) -> tuple[Optional[str], int]:
        """Create a contract on-chain. Returns (tx_sig, contract_id)."""
        contract_id = self._contract_count
        contract_pda, _ = _find_contract_pda(self.protocol_pda, contract_id)

        obj_hash = objective_hash or hashlib.sha256(f"obj-{contract_id}".encode()).digest()[:16]
        con_hash = constraints_hash or hashlib.sha256(f"con-{contract_id}".encode()).digest()[:16]
        domain_bytes = domain.encode("utf-8")[:32]
        deadline = int(time.time()) + 3600  # 1 hour from now

        data = _discriminator("create_contract")
        data += obj_hash[:16]
        data += con_hash[:16]
        data += min_tier.to_bytes(1, "little")
        data += reward_lamports.to_bytes(8, "little")
        data += penalty_lamports.to_bytes(8, "little")
        data += deadline.to_bytes(8, "little", signed=True)
        data += len(domain_bytes).to_bytes(4, "little") + domain_bytes

        ix = Instruction(
            PROGRAM_ID,
            data,
            [
                AccountMeta(contract_pda, is_signer=False, is_writable=True),
                AccountMeta(self.protocol_pda, is_signer=False, is_writable=True),
                AccountMeta(self.admin.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )
        sig = self._send(ix, [self.admin], f"create_contract #{contract_id}")
        if sig:
            self._contract_count += 1
        return sig, contract_id

    def accept_contract(self, contract_id: int, model_name: str) -> Optional[str]:
        """Agent accepts a contract on-chain."""
        agent_kp = self.get_or_create_agent_keypair(model_name)
        agent_pda, _ = _find_agent_pda(agent_kp.pubkey())
        contract_pda, _ = _find_contract_pda(self.protocol_pda, contract_id)

        data = _discriminator("accept_contract")
        ix = Instruction(
            PROGRAM_ID,
            data,
            [
                AccountMeta(contract_pda, is_signer=False, is_writable=True),
                AccountMeta(agent_pda, is_signer=False, is_writable=False),
                AccountMeta(self.protocol_pda, is_signer=False, is_writable=False),
                AccountMeta(agent_kp.pubkey(), is_signer=True, is_writable=True),
                AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            ],
        )
        return self._send(ix, [self.admin, agent_kp], f"accept #{contract_id} by {model_name}")

    def complete_contract(self, contract_id: int, model_name: str) -> Optional[str]:
        """Mark contract as completed — reward goes to agent."""
        agent_kp = self.get_or_create_agent_keypair(model_name)
        agent_pda, _ = _find_agent_pda(agent_kp.pubkey())
        contract_pda, _ = _find_contract_pda(self.protocol_pda, contract_id)

        data = _discriminator("complete_contract")
        ix = Instruction(
            PROGRAM_ID,
            data,
            [
                AccountMeta(contract_pda, is_signer=False, is_writable=True),
                AccountMeta(agent_pda, is_signer=False, is_writable=True),
                AccountMeta(self.protocol_pda, is_signer=False, is_writable=True),
                AccountMeta(agent_kp.pubkey(), is_signer=False, is_writable=True),
                AccountMeta(self.admin.pubkey(), is_signer=True, is_writable=False),
            ],
        )
        return self._send(ix, [self.admin], f"complete #{contract_id}")

    def fail_contract(self, contract_id: int, model_name: str) -> Optional[str]:
        """Mark contract as failed — penalty collected."""
        agent_kp = self.get_or_create_agent_keypair(model_name)
        contract_pda, _ = _find_contract_pda(self.protocol_pda, contract_id)
        agent_pda, _ = _find_agent_pda(agent_kp.pubkey())

        data = _discriminator("fail_contract")
        ix = Instruction(
            PROGRAM_ID,
            data,
            [
                AccountMeta(contract_pda, is_signer=False, is_writable=True),
                AccountMeta(agent_pda, is_signer=False, is_writable=True),
                AccountMeta(self.protocol_pda, is_signer=False, is_writable=True),
                AccountMeta(self.admin.pubkey(), is_signer=False, is_writable=True),
                AccountMeta(self.admin.pubkey(), is_signer=True, is_writable=True),
            ],
        )
        return self._send(ix, [self.admin], f"fail #{contract_id}")
