use anchor_lang::prelude::*;
use crate::state::*;
use crate::error::CgaeError;

pub fn handler(
    ctx: Context<RegisterAgent>,
    architecture_hash: [u8; 16],
    model_name: String,
) -> Result<()> {
    require!(model_name.len() <= AgentRecord::MAX_MODEL_NAME, CgaeError::ModelNameTooLong);

    let agent = &mut ctx.accounts.agent_record;
    agent.owner = ctx.accounts.owner.key();
    agent.architecture_hash = architecture_hash;
    agent.model_name = model_name;
    agent.current_tier = 0;
    agent.robustness = RobustnessVector::default();
    agent.registration_time = Clock::get()?.unix_timestamp;
    agent.last_audit_time = 0;
    agent.active = false;
    agent.total_earned = 0;
    agent.total_penalties = 0;
    agent.contracts_completed = 0;
    agent.contracts_failed = 0;
    agent.audit_cid = String::new();
    agent.bump = ctx.bumps.agent_record;

    let state = &mut ctx.accounts.protocol_state;
    state.agent_count += 1;

    Ok(())
}

#[derive(Accounts)]
pub struct RegisterAgent<'info> {
    #[account(
        init,
        payer = owner,
        space = AgentRecord::SIZE,
        seeds = [b"agent", owner.key().as_ref()],
        bump,
    )]
    pub agent_record: Account<'info, AgentRecord>,
    #[account(
        mut,
        seeds = [b"protocol"],
        bump = protocol_state.bump,
    )]
    pub protocol_state: Account<'info, ProtocolState>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub system_program: Program<'info, System>,
}
