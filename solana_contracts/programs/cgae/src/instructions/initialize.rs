use anchor_lang::prelude::*;
use crate::state::*;

pub fn handler(ctx: Context<Initialize>) -> Result<()> {
    let state = &mut ctx.accounts.protocol_state;
    state.admin = ctx.accounts.admin.key();
    state.thresholds = TierThresholds::default();
    state.budget_ceilings = BudgetCeilings::default();
    state.agent_count = 0;
    state.contract_count = 0;
    state.total_rewards_paid = 0;
    state.total_penalties_collected = 0;
    state.bump = ctx.bumps.protocol_state;
    Ok(())
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = admin,
        space = ProtocolState::SIZE,
        seeds = [b"protocol"],
        bump,
    )]
    pub protocol_state: Account<'info, ProtocolState>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}
