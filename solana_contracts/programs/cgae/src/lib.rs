pub mod error;
pub mod instructions;
pub mod state;

use anchor_lang::prelude::*;
pub use instructions::*;

declare_id!("Aydqk82Wt1Cni6GQHTSJimtVskZ9PqvA6QyhtRjcRN3a");

#[program]
pub mod cgae {
    use super::*;

    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        instructions::initialize::handler(ctx)
    }

    pub fn register_agent(
        ctx: Context<RegisterAgent>,
        architecture_hash: [u8; 16],
        model_name: String,
    ) -> Result<()> {
        instructions::register_agent::handler(ctx, architecture_hash, model_name)
    }

    pub fn certify_agent(
        ctx: Context<CertifyAgent>,
        cc: u16,
        er: u16,
        as_: u16,
        ih: u16,
        audit_cid: String,
    ) -> Result<()> {
        instructions::certify_agent::handler(ctx, cc, er, as_, ih, audit_cid)
    }

    pub fn create_contract(
        ctx: Context<CreateContract>,
        objective_hash: [u8; 32],
        constraints_hash: [u8; 32],
        min_tier: u8,
        reward: u64,
        penalty: u64,
        deadline: i64,
        domain: String,
    ) -> Result<()> {
        instructions::create_contract::handler(ctx, objective_hash, constraints_hash, min_tier, reward, penalty, deadline, domain)
    }

    pub fn accept_contract(ctx: Context<AcceptContract>) -> Result<()> {
        instructions::accept_contract::handler(ctx)
    }

    pub fn complete_contract(ctx: Context<CompleteContract>) -> Result<()> {
        instructions::complete_contract::handler(ctx)
    }

    pub fn fail_contract(ctx: Context<FailContract>) -> Result<()> {
        instructions::fail_contract::handler(ctx)
    }

    pub fn expire_contract(ctx: Context<ExpireContract>) -> Result<()> {
        instructions::expire_contract::handler(ctx)
    }
}
