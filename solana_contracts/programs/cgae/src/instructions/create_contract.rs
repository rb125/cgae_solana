use anchor_lang::prelude::*;
use anchor_lang::system_program;
use crate::state::*;
use crate::error::CgaeError;

pub fn handler(
    ctx: Context<CreateContract>,
    objective_hash: [u8; 32],
    constraints_hash: [u8; 32],
    min_tier: u8,
    reward: u64,
    penalty: u64,
    deadline: i64,
    domain: String,
) -> Result<()> {
    require!(min_tier >= 1 && min_tier <= 5, CgaeError::InvalidTier);
    require!(deadline > Clock::get()?.unix_timestamp, CgaeError::DeadlinePassed);
    require!(domain.len() <= EconomicContract::MAX_DOMAIN, CgaeError::DomainTooLong);
    require!(reward > 0, CgaeError::InsufficientCollateral);

    let state = &mut ctx.accounts.protocol_state;
    let contract_id = state.contract_count;
    state.contract_count += 1;

    let contract = &mut ctx.accounts.economic_contract;
    contract.contract_id = contract_id;
    contract.issuer = ctx.accounts.issuer.key();
    contract.assigned_agent = Pubkey::default();
    contract.objective_hash = objective_hash;
    contract.constraints_hash = constraints_hash;
    contract.min_tier = min_tier;
    contract.reward = reward;
    contract.penalty = penalty;
    contract.deadline = deadline;
    contract.created_at = Clock::get()?.unix_timestamp;
    contract.status = ContractStatus::Open;
    contract.domain = domain;
    contract.bump = ctx.bumps.economic_contract;

    // Transfer reward into the contract PDA itself (program-owned, so we can debit later)
    system_program::transfer(
        CpiContext::new(
            system_program::ID,
            system_program::Transfer {
                from: ctx.accounts.issuer.to_account_info(),
                to: ctx.accounts.economic_contract.to_account_info(),
            },
        ),
        reward,
    )?;

    Ok(())
}

#[derive(Accounts)]
pub struct CreateContract<'info> {
    #[account(
        init,
        payer = issuer,
        space = EconomicContract::SIZE,
        seeds = [b"contract", protocol_state.key().as_ref(), &protocol_state.contract_count.to_le_bytes()],
        bump,
    )]
    pub economic_contract: Account<'info, EconomicContract>,
    #[account(
        mut,
        seeds = [b"protocol"],
        bump = protocol_state.bump,
    )]
    pub protocol_state: Account<'info, ProtocolState>,
    #[account(mut)]
    pub issuer: Signer<'info>,
    pub system_program: Program<'info, System>,
}
