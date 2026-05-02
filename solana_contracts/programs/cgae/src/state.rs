use anchor_lang::prelude::*;

/// Robustness vector R = (CC, ER, AS, IH), each in [0, 10000].
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, Default, Debug, PartialEq)]
pub struct RobustnessVector {
    pub cc: u16,
    pub er: u16,
    pub as_: u16,
    pub ih: u16,
}

/// Tier thresholds per dimension. Index 0 = T0 (always 0), up to T5.
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, Debug)]
pub struct TierThresholds {
    pub cc: [u16; 6],
    pub er: [u16; 6],
    pub as_: [u16; 6],
    pub ih_threshold: u16,
}

impl Default for TierThresholds {
    fn default() -> Self {
        Self {
            cc: [0, 3000, 5000, 6500, 8000, 9000],
            er: [0, 3000, 5000, 6500, 8000, 9000],
            as_: [0, 2500, 4500, 6000, 7500, 8500],
            ih_threshold: 5000,
        }
    }
}

/// Budget ceilings per tier in lamports.
/// T0=0, T1=0.0002 SOL, T2=0.002, T3=0.02, T4=0.2, T5=2.0
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, Debug)]
pub struct BudgetCeilings {
    pub ceilings: [u64; 6],
}

impl Default for BudgetCeilings {
    fn default() -> Self {
        Self {
            ceilings: [
                0,
                200_000,       // 0.0002 SOL
                2_000_000,     // 0.002 SOL
                20_000_000,    // 0.02 SOL
                200_000_000,   // 0.2 SOL
                2_000_000_000, // 2.0 SOL
            ],
        }
    }
}

/// Global protocol state — one per deployment.
/// PDA seeds: [b"protocol"]
#[account]
pub struct ProtocolState {
    pub admin: Pubkey,
    pub thresholds: TierThresholds,
    pub budget_ceilings: BudgetCeilings,
    pub agent_count: u32,
    pub contract_count: u32,
    pub total_rewards_paid: u64,
    pub total_penalties_collected: u64,
    pub bump: u8,
}

impl ProtocolState {
    pub const SIZE: usize = 8  // discriminator
        + 32                   // admin
        + (2 * 6) * 3 + 2     // thresholds: 3 arrays of 6 u16 + ih_threshold
        + 8 * 6               // budget_ceilings
        + 4 + 4               // agent_count, contract_count
        + 8 + 8               // total_rewards_paid, total_penalties_collected
        + 1;                   // bump
}

/// Agent record — one PDA per agent wallet.
/// PDA seeds: [b"agent", owner.key()]
#[account]
pub struct AgentRecord {
    pub owner: Pubkey,
    pub architecture_hash: [u8; 16],
    pub model_name: String,         // max 64 chars
    pub current_tier: u8,
    pub robustness: RobustnessVector,
    pub registration_time: i64,
    pub last_audit_time: i64,
    pub active: bool,
    pub total_earned: u64,
    pub total_penalties: u64,
    pub contracts_completed: u32,
    pub contracts_failed: u32,
    pub audit_cid: String,          // max 128 chars — Arweave TX ID or IPFS CID
    pub bump: u8,
}

impl AgentRecord {
    pub const MAX_MODEL_NAME: usize = 64;
    pub const MAX_AUDIT_CID: usize = 128;
    pub const SIZE: usize = 8  // discriminator
        + 32                   // owner
        + 16                   // architecture_hash
        + 4 + Self::MAX_MODEL_NAME  // model_name (String = 4-byte len + data)
        + 1                    // current_tier
        + 8                    // robustness (4 x u16)
        + 8 + 8               // registration_time, last_audit_time
        + 1                    // active
        + 8 + 8               // total_earned, total_penalties
        + 4 + 4               // contracts_completed, contracts_failed
        + 4 + Self::MAX_AUDIT_CID  // audit_cid
        + 1;                   // bump
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Debug)]
pub enum ContractStatus {
    Open,
    Assigned,
    Completed,
    Failed,
    Expired,
}

/// Economic contract with escrow.
/// PDA seeds: [b"contract", protocol.key(), contract_id.to_le_bytes()]
#[account]
pub struct EconomicContract {
    pub contract_id: u32,
    pub issuer: Pubkey,
    pub assigned_agent: Pubkey,
    pub objective_hash: [u8; 32],   // hash of objective text (keep on-chain data small)
    pub constraints_hash: [u8; 32],
    pub min_tier: u8,
    pub reward: u64,
    pub penalty: u64,
    pub deadline: i64,
    pub created_at: i64,
    pub status: ContractStatus,
    pub domain: String,             // max 32 chars
    pub bump: u8,
}

impl EconomicContract {
    pub const MAX_DOMAIN: usize = 32;
    pub const SIZE: usize = 8  // discriminator
        + 4                    // contract_id
        + 32 + 32             // issuer, assigned_agent
        + 32 + 32             // objective_hash, constraints_hash
        + 1                    // min_tier
        + 8 + 8               // reward, penalty
        + 8 + 8               // deadline, created_at
        + 1 + 1               // status (enum), bump
        + 4 + Self::MAX_DOMAIN; // domain
}

/// Escrow vault PDA — holds SOL for a contract.
/// PDA seeds: [b"vault", contract_pda.key()]
/// This is just a system-owned PDA; no account struct needed.
/// We track it via seeds.

/// Compute tier from robustness vector using weakest-link gate function.
/// f(R) = T_k where k = min(g1(CC), g2(ER), g3(AS))
/// IH* < threshold triggers T0.
pub fn compute_tier(r: &RobustnessVector, t: &TierThresholds) -> u8 {
    if r.ih < t.ih_threshold {
        return 0;
    }
    let g_cc = step_function(r.cc, &t.cc);
    let g_er = step_function(r.er, &t.er);
    let g_as = step_function(r.as_, &t.as_);
    g_cc.min(g_er).min(g_as)
}

/// Step function g_i(x) = max{k : x >= theta_i^k}
fn step_function(score: u16, thresholds: &[u16; 6]) -> u8 {
    let mut tier = 0u8;
    for k in 1..6 {
        if score >= thresholds[k] {
            tier = k as u8;
        } else {
            break;
        }
    }
    tier
}
