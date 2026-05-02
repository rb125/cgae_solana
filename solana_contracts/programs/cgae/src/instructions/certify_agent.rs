use anchor_lang::prelude::*;
use crate::state::*;
use crate::error::CgaeError;

pub fn handler(
    ctx: Context<CertifyAgent>,
    cc: u16,
    er: u16,
    as_: u16,
    ih: u16,
    audit_cid: String,
) -> Result<()> {
    require!(cc <= 10000 && er <= 10000 && as_ <= 10000 && ih <= 10000, CgaeError::ScoreOutOfRange);
    require!(audit_cid.len() <= AgentRecord::MAX_AUDIT_CID, CgaeError::AuditCidTooLong);

    let state = &ctx.accounts.protocol_state;
    require!(ctx.accounts.admin.key() == state.admin, CgaeError::Unauthorized);

    let r = RobustnessVector { cc, er, as_: as_, ih };
    let tier = compute_tier(&r, &state.thresholds);

    let agent = &mut ctx.accounts.agent_record;
    agent.robustness = r;
    agent.current_tier = tier;
    agent.last_audit_time = Clock::get()?.unix_timestamp;
    agent.active = tier > 0;
    agent.audit_cid = audit_cid;

    Ok(())
}

#[derive(Accounts)]
pub struct CertifyAgent<'info> {
    #[account(
        mut,
        seeds = [b"agent", agent_record.owner.as_ref()],
        bump = agent_record.bump,
    )]
    pub agent_record: Account<'info, AgentRecord>,
    #[account(
        seeds = [b"protocol"],
        bump = protocol_state.bump,
    )]
    pub protocol_state: Account<'info, ProtocolState>,
    pub admin: Signer<'info>,
}
