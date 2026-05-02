use anchor_lang::prelude::*;
use crate::state::*;
use crate::error::CgaeError;

pub fn handler(ctx: Context<ExpireContract>) -> Result<()> {
    let contract = &mut ctx.accounts.economic_contract;
    require!(contract.status == ContractStatus::Open, CgaeError::ContractNotOpen);
    require!(Clock::get()?.unix_timestamp >= contract.deadline, CgaeError::NotExpired);

    contract.status = ContractStatus::Expired;

    // Return escrowed reward to issuer
    contract.sub_lamports(contract.reward)?;
    ctx.accounts.issuer.add_lamports(contract.reward)?;

    Ok(())
}

#[derive(Accounts)]
pub struct ExpireContract<'info> {
    #[account(
        mut,
        seeds = [b"contract", protocol_state.key().as_ref(), &economic_contract.contract_id.to_le_bytes()],
        bump = economic_contract.bump,
    )]
    pub economic_contract: Account<'info, EconomicContract>,
    #[account(
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
    pub signer: Signer<'info>,
}
