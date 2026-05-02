use cgae::accounts::*;
use cgae::state::*;
use anchor_lang::{AccountDeserialize, InstructionData, ToAccountMetas};
use litesvm::LiteSVM;
use solana_keypair::Keypair;
use solana_message::Message;
use solana_signer::Signer;
use solana_transaction::Transaction;

fn find_so() -> &'static str {
    ["target/deploy/cgae.so", "../target/deploy/cgae.so", "../../target/deploy/cgae.so"]
        .iter()
        .copied()
        .find(|p| std::path::Path::new(p).exists())
        .expect("cgae.so not found — run `anchor build` first")
}

fn pda(seeds: &[&[u8]], program_id: &solana_pubkey::Pubkey) -> solana_pubkey::Pubkey {
    solana_pubkey::Pubkey::find_program_address(seeds, program_id).0
}

fn send(svm: &mut LiteSVM, signers: &[&Keypair], ixs: Vec<solana_instruction::Instruction>) {
    let blockhash = svm.latest_blockhash();
    let payer = signers[0].pubkey();
    let tx = Transaction::new(signers, Message::new(&ixs, Some(&payer)), blockhash);
    svm.send_transaction(tx).unwrap();
}

fn sys() -> solana_pubkey::Pubkey {
    solana_system_interface::program::id()
}

/// Setup: initialize protocol, register agent, certify to given scores.
fn setup_with_agent(
    svm: &mut LiteSVM,
    program_id: solana_pubkey::Pubkey,
    admin: &Keypair,
    agent_wallet: &Keypair,
    cc: u16, er: u16, as_: u16, ih: u16,
) -> (solana_pubkey::Pubkey, solana_pubkey::Pubkey) {
    let protocol_state = pda(&[b"protocol"], &program_id);
    let agent_pda = pda(&[b"agent", agent_wallet.pubkey().as_ref()], &program_id);

    send(svm, &[admin], vec![solana_instruction::Instruction {
        program_id,
        accounts: Initialize { protocol_state, admin: admin.pubkey(), system_program: sys() }.to_account_metas(None),
        data: cgae::instruction::Initialize {}.data(),
    }]);

    send(svm, &[agent_wallet], vec![solana_instruction::Instruction {
        program_id,
        accounts: RegisterAgent { agent_record: agent_pda, protocol_state, owner: agent_wallet.pubkey(), system_program: sys() }.to_account_metas(None),
        data: cgae::instruction::RegisterAgent { architecture_hash: [1u8; 16], model_name: "test-model".into() }.data(),
    }]);

    send(svm, &[admin], vec![solana_instruction::Instruction {
        program_id,
        accounts: CertifyAgent { agent_record: agent_pda, protocol_state, admin: admin.pubkey() }.to_account_metas(None),
        data: cgae::instruction::CertifyAgent { cc, er, as_: as_, ih, audit_cid: "cid_test".into() }.data(),
    }]);

    (protocol_state, agent_pda)
}

#[test]
fn test_initialize() {
    let program_id = cgae::id();
    let mut svm = LiteSVM::new();
    svm.add_program_from_file(program_id, find_so()).unwrap();

    let admin = Keypair::new();
    svm.airdrop(&admin.pubkey(), 10_000_000_000).unwrap();

    let protocol_state = pda(&[b"protocol"], &program_id);
    send(&mut svm, &[&admin], vec![solana_instruction::Instruction {
        program_id,
        accounts: Initialize { protocol_state, admin: admin.pubkey(), system_program: sys() }.to_account_metas(None),
        data: cgae::instruction::Initialize {}.data(),
    }]);

    let acct = svm.get_account(&protocol_state).unwrap();
    let state = ProtocolState::try_deserialize(&mut &acct.data[..]).unwrap();
    assert_eq!(state.admin, admin.pubkey());
    assert_eq!(state.agent_count, 0);
    assert_eq!(state.thresholds.cc, [0, 3000, 5000, 6500, 8000, 9000]);
}

#[test]
fn test_register_and_certify_agent() {
    let program_id = cgae::id();
    let mut svm = LiteSVM::new();
    svm.add_program_from_file(program_id, find_so()).unwrap();

    let admin = Keypair::new();
    let agent_wallet = Keypair::new();
    svm.airdrop(&admin.pubkey(), 10_000_000_000).unwrap();
    svm.airdrop(&agent_wallet.pubkey(), 10_000_000_000).unwrap();

    let (protocol_state, agent_pda) = setup_with_agent(
        &mut svm, program_id, &admin, &agent_wallet,
        8500, 8200, 7800, 9000, // → T4
    );
    let _ = protocol_state;

    let acct = svm.get_account(&agent_pda).unwrap();
    let agent = AgentRecord::try_deserialize(&mut &acct.data[..]).unwrap();
    assert_eq!(agent.current_tier, 4);
    assert!(agent.active);
    assert_eq!(agent.audit_cid, "cid_test");
}

#[test]
fn test_weakest_link_gate() {
    let t = TierThresholds::default();

    // CC=T5, ER=T1, AS=T4 → min = T1
    assert_eq!(compute_tier(&RobustnessVector { cc: 9500, er: 3500, as_: 8000, ih: 9000 }, &t), 1);
    // IH below threshold → T0
    assert_eq!(compute_tier(&RobustnessVector { cc: 9500, er: 9500, as_: 9500, ih: 4000 }, &t), 0);
    // All maxed → T5
    assert_eq!(compute_tier(&RobustnessVector { cc: 9500, er: 9500, as_: 9500, ih: 9000 }, &t), 5);
    // All zero → T0
    assert_eq!(compute_tier(&RobustnessVector { cc: 0, er: 0, as_: 0, ih: 0 }, &t), 0);
}

#[test]
fn test_full_contract_lifecycle() {
    let program_id = cgae::id();
    let mut svm = LiteSVM::new();
    svm.add_program_from_file(program_id, find_so()).unwrap();

    let admin = Keypair::new();
    let issuer = Keypair::new();
    let agent_wallet = Keypair::new();
    svm.airdrop(&admin.pubkey(), 10_000_000_000).unwrap();
    svm.airdrop(&issuer.pubkey(), 10_000_000_000).unwrap();
    svm.airdrop(&agent_wallet.pubkey(), 10_000_000_000).unwrap();

    // Setup: agent certified to T2 (CC=5500, ER=5500, AS=5000, IH=7000)
    let (protocol_state, agent_pda) = setup_with_agent(
        &mut svm, program_id, &admin, &agent_wallet,
        5500, 5500, 5000, 7000,
    );

    // Verify T2
    let acct = svm.get_account(&agent_pda).unwrap();
    let agent = AgentRecord::try_deserialize(&mut &acct.data[..]).unwrap();
    assert_eq!(agent.current_tier, 2);

    // Create contract
    let contract_pda = pda(&[b"contract", protocol_state.as_ref(), &0u32.to_le_bytes()], &program_id);
    let reward = 1_000_000u64;
    let penalty = 100_000u64;

    send(&mut svm, &[&issuer], vec![solana_instruction::Instruction {
        program_id,
        accounts: CreateContract {
            economic_contract: contract_pda,
            protocol_state,
            issuer: issuer.pubkey(),
            system_program: sys(),
        }.to_account_metas(None),
        data: cgae::instruction::CreateContract {
            objective_hash: [0xAA; 32],
            constraints_hash: [0xBB; 32],
            min_tier: 1,
            reward,
            penalty,
            deadline: 9999999999i64,
            domain: "coding".into(),
        }.data(),
    }]);

    // Contract PDA should hold reward lamports (on top of rent)
    let contract_lamports_after_create = svm.get_account(&contract_pda).unwrap().lamports;
    assert!(contract_lamports_after_create >= reward);

    let agent_balance_before = svm.get_account(&agent_wallet.pubkey()).unwrap().lamports;

    // Agent accepts
    send(&mut svm, &[&agent_wallet], vec![solana_instruction::Instruction {
        program_id,
        accounts: AcceptContract {
            economic_contract: contract_pda,
            agent_record: agent_pda,
            protocol_state,
            agent_wallet: agent_wallet.pubkey(),
            system_program: sys(),
        }.to_account_metas(None),
        data: cgae::instruction::AcceptContract {}.data(),
    }]);

    let acct = svm.get_account(&contract_pda).unwrap();
    let contract = EconomicContract::try_deserialize(&mut &acct.data[..]).unwrap();
    assert_eq!(contract.status, ContractStatus::Assigned);
    assert_eq!(contract.assigned_agent, agent_wallet.pubkey());

    // Admin completes contract → agent gets reward + collateral back
    send(&mut svm, &[&admin], vec![solana_instruction::Instruction {
        program_id,
        accounts: CompleteContract {
            economic_contract: contract_pda,
            agent_record: agent_pda,
            protocol_state,
            agent_wallet: agent_wallet.pubkey().into(),
            admin: admin.pubkey(),
        }.to_account_metas(None),
        data: cgae::instruction::CompleteContract {}.data(),
    }]);

    let agent_balance_after = svm.get_account(&agent_wallet.pubkey()).unwrap().lamports;
    // Net: paid penalty + tx fees, got back penalty + reward
    // So balance_after > balance_before - penalty (since reward > fees)
    assert!(agent_balance_after > agent_balance_before - penalty);

    let acct = svm.get_account(&agent_pda).unwrap();
    let agent = AgentRecord::try_deserialize(&mut &acct.data[..]).unwrap();
    assert_eq!(agent.contracts_completed, 1);
    assert_eq!(agent.total_earned, reward);

    // Verify protocol stats
    let acct = svm.get_account(&protocol_state).unwrap();
    let state = ProtocolState::try_deserialize(&mut &acct.data[..]).unwrap();
    assert_eq!(state.total_rewards_paid, reward);
    assert_eq!(state.agent_count, 1);
    assert_eq!(state.contract_count, 1);
}

#[test]
fn test_tier_too_low_rejected() {
    let program_id = cgae::id();
    let mut svm = LiteSVM::new();
    svm.add_program_from_file(program_id, find_so()).unwrap();

    let admin = Keypair::new();
    let issuer = Keypair::new();
    let agent_wallet = Keypair::new();
    svm.airdrop(&admin.pubkey(), 10_000_000_000).unwrap();
    svm.airdrop(&issuer.pubkey(), 10_000_000_000).unwrap();
    svm.airdrop(&agent_wallet.pubkey(), 10_000_000_000).unwrap();

    // Agent at T1
    let (protocol_state, agent_pda) = setup_with_agent(
        &mut svm, program_id, &admin, &agent_wallet,
        3500, 3500, 3000, 7000,
    );

    let acct = svm.get_account(&agent_pda).unwrap();
    let agent = AgentRecord::try_deserialize(&mut &acct.data[..]).unwrap();
    assert_eq!(agent.current_tier, 1);

    // Create T3 contract
    let contract_pda = pda(&[b"contract", protocol_state.as_ref(), &0u32.to_le_bytes()], &program_id);
    send(&mut svm, &[&issuer], vec![solana_instruction::Instruction {
        program_id,
        accounts: CreateContract {
            economic_contract: contract_pda,
            protocol_state,
            issuer: issuer.pubkey(),
            system_program: sys(),
        }.to_account_metas(None),
        data: cgae::instruction::CreateContract {
            objective_hash: [0; 32],
            constraints_hash: [0; 32],
            min_tier: 3,
            reward: 500_000,
            penalty: 50_000,
            deadline: 9999999999i64,
            domain: "math".into(),
        }.data(),
    }]);

    // Agent tries to accept T3 contract with T1 → should fail
    let blockhash = svm.latest_blockhash();
    let ix = solana_instruction::Instruction {
        program_id,
        accounts: AcceptContract {
            economic_contract: contract_pda,
            agent_record: agent_pda,
            protocol_state,
            agent_wallet: agent_wallet.pubkey(),
            system_program: sys(),
        }.to_account_metas(None),
        data: cgae::instruction::AcceptContract {}.data(),
    };
    let tx = Transaction::new(&[&agent_wallet], Message::new(&[ix], Some(&agent_wallet.pubkey())), blockhash);
    let result = svm.send_transaction(tx);
    assert!(result.is_err(), "T1 agent should not accept T3 contract");
}
