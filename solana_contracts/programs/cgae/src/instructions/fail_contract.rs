use anchor_lang::prelude::*;
use crate::state::*;
use crate::error::CgaeError;

pub fn handler(ctx: Context<FailContract>) -> Result<()> {
    let state = &mut ctx.accounts.protocol_state;
    require!(ctx.accounts.admin.key() == state.admin, CgaeError::Unauthorized);

    let contract = &mut ctx.accounts.economic_contract;
    require!(contract.status == ContractStatus::Assigned, CgaeError::ContractNotAssigned);

    contract.status = ContractStatus::Failed;
    state.total_penalties_collected += contract.penalty;

    // Return escrowed reward to issuer
    contract.sub_lamports(contract.reward)?;
    ctx.accounts.issuer.add_lamports(contract.reward)?;

    // Penalty forfeited — send to admin
    if contract.penalty > 0 {
        contract.sub_lamports(contract.penalty)?;
        ctx.accounts.admin.add_lamports(contract.penalty)?;
    }

    // Update agent stats
    let agent = &mut ctx.accounts.agent_record;
    agent.contracts_failed += 1;
    agent.total_penalties += contract.penalty;

    Ok(())
}

#[derive(Accounts)]
pub struct FailContract<'info> {
    #[account(
        mut,
        seeds = [b"contract", protocol_state.key().as_ref(), &economic_contract.contract_id.to_le_bytes()],
        bump = economic_contract.bump,
    )]
    pub economic_contract: Account<'info, EconomicContract>,
    #[account(
        mut,
        seeds = [b"agent", economic_contract.assigned_agent.as_ref()],
        bump = agent_record.bump,
    )]
    pub agent_record: Account<'info, AgentRecord>,
    #[account(
        mut,
        seeds = [b"protocol"],
        bump = protocol_state.bump,
    )]
    pub protocol_state: Account<'info, ProtocolState>,
    /// CHECK: Original issuer to receive reward refund. Validated via constraint.
    #[account(
        mut,
        constraint = issuer.key() == economic_contract.issuer,
    )]
    pub issuer: UncheckedAccount<'info>,
    #[account(mut)]
    pub admin: Signer<'info>,
}
