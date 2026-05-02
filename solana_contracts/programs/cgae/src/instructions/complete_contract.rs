use anchor_lang::prelude::*;
use crate::state::*;
use crate::error::CgaeError;

pub fn handler(ctx: Context<CompleteContract>) -> Result<()> {
    let state = &mut ctx.accounts.protocol_state;
    require!(ctx.accounts.admin.key() == state.admin, CgaeError::Unauthorized);

    let contract = &mut ctx.accounts.economic_contract;
    require!(contract.status == ContractStatus::Assigned, CgaeError::ContractNotAssigned);

    contract.status = ContractStatus::Completed;

    let payout = contract.reward + contract.penalty;
    state.total_rewards_paid += contract.reward;

    // Transfer from contract PDA (program-owned) to agent wallet
    contract.sub_lamports(payout)?;
    ctx.accounts.agent_wallet.add_lamports(payout)?;

    // Update agent stats
    let agent = &mut ctx.accounts.agent_record;
    agent.contracts_completed += 1;
    agent.total_earned += contract.reward;

    Ok(())
}

#[derive(Accounts)]
pub struct CompleteContract<'info> {
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
    /// CHECK: Agent wallet to receive payout. Validated via constraint.
    #[account(
        mut,
        constraint = agent_wallet.key() == economic_contract.assigned_agent,
    )]
    pub agent_wallet: UncheckedAccount<'info>,
    pub admin: Signer<'info>,
}
