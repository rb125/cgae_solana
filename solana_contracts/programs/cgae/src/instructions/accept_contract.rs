use anchor_lang::prelude::*;
use anchor_lang::system_program;
use crate::state::*;
use crate::error::CgaeError;

pub fn handler(ctx: Context<AcceptContract>) -> Result<()> {
    let agent = &ctx.accounts.agent_record;
    let state = &ctx.accounts.protocol_state;

    // Read contract fields before mutable borrow
    let status = ctx.accounts.economic_contract.status;
    let deadline = ctx.accounts.economic_contract.deadline;
    let min_tier = ctx.accounts.economic_contract.min_tier;
    let penalty = ctx.accounts.economic_contract.penalty;

    require!(status == ContractStatus::Open, CgaeError::ContractNotOpen);
    require!(Clock::get()?.unix_timestamp < deadline, CgaeError::DeadlinePassed);
    require!(agent.active, CgaeError::NotActive);
    require!(agent.current_tier >= min_tier, CgaeError::TierTooLow);

    // Budget ceiling check (Theorem 1)
    let ceiling = state.budget_ceilings.ceilings[agent.current_tier as usize];
    require!(penalty <= ceiling, CgaeError::BudgetCeilingExceeded);

    // Agent deposits penalty collateral into contract PDA
    if penalty > 0 {
        system_program::transfer(
            CpiContext::new(
                system_program::ID,
                system_program::Transfer {
                    from: ctx.accounts.agent_wallet.to_account_info(),
                    to: ctx.accounts.economic_contract.to_account_info(),
                },
            ),
            penalty,
        )?;
    }

    let contract = &mut ctx.accounts.economic_contract;
    contract.assigned_agent = agent.owner;
    contract.status = ContractStatus::Assigned;

    Ok(())
}

#[derive(Accounts)]
pub struct AcceptContract<'info> {
    #[account(
        mut,
        seeds = [b"contract", protocol_state.key().as_ref(), &economic_contract.contract_id.to_le_bytes()],
        bump = economic_contract.bump,
    )]
    pub economic_contract: Account<'info, EconomicContract>,
    #[account(
        seeds = [b"agent", agent_wallet.key().as_ref()],
        bump = agent_record.bump,
    )]
    pub agent_record: Account<'info, AgentRecord>,
    #[account(
        seeds = [b"protocol"],
        bump = protocol_state.bump,
    )]
    pub protocol_state: Account<'info, ProtocolState>,
    #[account(mut)]
    pub agent_wallet: Signer<'info>,
    pub system_program: Program<'info, System>,
}
