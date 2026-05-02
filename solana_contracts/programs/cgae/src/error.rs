use anchor_lang::prelude::*;

#[error_code]
pub enum CgaeError {
    #[msg("Agent already registered")]
    AlreadyRegistered,
    #[msg("Agent not registered")]
    NotRegistered,
    #[msg("Agent not active")]
    NotActive,
    #[msg("Not authorized")]
    Unauthorized,
    #[msg("Model name too long (max 64)")]
    ModelNameTooLong,
    #[msg("Audit CID too long (max 128)")]
    AuditCidTooLong,
    #[msg("Domain too long (max 32)")]
    DomainTooLong,
    #[msg("Invalid tier (must be 1-5)")]
    InvalidTier,
    #[msg("Contract not open")]
    ContractNotOpen,
    #[msg("Contract not assigned")]
    ContractNotAssigned,
    #[msg("Deadline must be in the future")]
    DeadlinePassed,
    #[msg("Agent tier too low for this contract")]
    TierTooLow,
    #[msg("Would exceed budget ceiling (Theorem 1)")]
    BudgetCeilingExceeded,
    #[msg("Insufficient penalty collateral")]
    InsufficientCollateral,
    #[msg("Contract not yet expired")]
    NotExpired,
    #[msg("Score out of range (0-10000)")]
    ScoreOutOfRange,
}
