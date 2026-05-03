---
title: CGAE Backend
emoji: 🚀
colorFrom: purple
colorTo: indigo
sdk: docker
app_file: hf_backend/app.py
pinned: false
---
# Comprehension-Gated Agent Economy (CGAE)

* **arXiv Paper:** [The Comprehension-Gated Agent Economy (CGAE): A Robustness-First Architecture](https://arxiv.org/abs/2603.15639)

## 📺 Technical Walkthrough & Demo

[![CGAE Technical Walkthrough](https://img.youtube.com/vi/E3jCNHC39-s/0.jpg)](https://youtu.be/E3jCNHC39-s)

---

**A Robustness-First Architecture for AI Economic Agency on Solana**

CGAE is a formal architecture where an AI agent's economic permissions are upper-bounded by verified comprehension, not capability benchmarks. Agents earn access to higher-value contracts by demonstrating robustness across three orthogonal dimensions: constraint compliance ([CDCT](https://arxiv.org/abs/2512.17920)), epistemic integrity ([DDFT](https://arxiv.org/abs/2512.23850)), and behavioral alignment (AGT). A weakest-link gate function ensures no dimension can be compensated by another.

This repository implements the CGAE protocol with a core economy engine, an Anchor program on Solana Devnet, a v2 autonomous agent architecture, live diagnostic framework integration, and a real-time dashboard.

**Paper**: Baxi (2026). *The Comprehension-Gated Agent Economy: A Robustness-First Architecture for AI Economic Agency.*

**Evaluation Framework Papers**:
- CDCT (Compression-Decay Comprehension Test): https://arxiv.org/abs/2512.17920
- DDFT (Drill-Down Fabrication Test): https://arxiv.org/abs/2512.23850

**Quick start:**
```bash
./scripts/run_demo_hosted.sh 10     # fixed rounds
./scripts/run_demo_hosted.sh --live # continuous mode
```

---

## Solana Integration

CGAE uses **Solana Devnet** for on-chain agent registry, escrow, and audit certificate anchoring.

| Layer | What | How |
|-------|------|-----|
| **On-chain program** | Agent identity, robustness certification, tier assignment, escrow | Single Anchor program `cgae` on Solana Devnet |
| **Audit storage** | Immutable audit certificate JSON (CDCT+DDFT+EECT results) | IPFS via Pinata — CID stored on-chain |

**Registration flow per agent:**
```
audit_live() → [CC, ER, AS, IH] → audit_cert.json
     ↓
Pinata IPFS upload → CID
     ↓
register_agent + certify_agent instructions → Solana Devnet
     ↓
create_contract / accept_contract / complete_contract per task → SOL settlement
```

Anyone can verify: fetch the CID from the agent's on-chain PDA, retrieve the JSON from IPFS, and confirm the robustness scores match the on-chain vector.

```
Program ID : Aydqk82Wt1Cni6GQHTSJimtVskZ9PqvA6QyhtRjcRN3a
Explorer   : https://solscan.io/account/Aydqk82Wt1Cni6GQHTSJimtVskZ9PqvA6QyhtRjcRN3a?cluster=devnet
Deployed   : contracts/deployed.json
```

---

## Repository Structure

```
cgae/
├── README.md                       # This file
├── ARCHITECTURE.md                 # Architectural design document
├── .env.example                    # Environment variable template
├── requirements.txt                # Python dependencies
│
├── cgae_engine/                    # Core protocol engine
│   ├── gate.py                     # Weakest-link gate function (Def 6, Eq 6-7)
│   ├── temporal.py                 # Temporal decay + stochastic re-auditing (Eq 8-10)
│   ├── registry.py                 # Agent identity and certification lifecycle
│   ├── contracts.py                # CGAE contracts with escrow and budget ceilings
│   ├── marketplace.py              # Tier-distributed task demand generation
│   ├── economy.py                  # Top-level coordinator (full economic loop)
│   ├── audit.py                    # Bridges CDCT/DDFT/EECT → robustness vectors
│   ├── solana_client.py            # Python bridge to the Anchor program
│   ├── llm_agent.py                # LLMAgent (Azure OpenAI / AI Foundry / Bedrock)
│   ├── models_config.py            # 11 model configurations
│   ├── tasks.py                    # 16 tasks with machine-verifiable constraints
│   └── verifier.py                 # Two-layer verification (algorithmic + jury LLM)
│
├── agents/                         # Agent implementations
│   ├── base.py                     # Abstract BaseAgent interface
│   ├── strategies.py               # Strategy archetypes
│   └── autonomous.py               # AutonomousAgent v2 (PerceptionLayer,
│                                   #   AccountingLayer, PlanningLayer, ExecutionLayer)
│
├── solana_contracts/               # Anchor program (Solana Devnet)
│   ├── programs/cgae/src/
│   │   ├── lib.rs                  # Program entrypoint (8 instructions)
│   │   ├── state.rs                # Account structs + gate function
│   │   ├── error.rs                # Custom errors
│   │   └── instructions/           # initialize, register_agent, certify_agent,
│   │                               # create_contract, accept_contract,
│   │                               # complete_contract, fail_contract, expire_contract
│   └── programs/cgae/tests/        # LiteSVM integration tests (6 tests)
│
├── storage/
│   └── solana_store.py             # IPFS upload via Pinata
│
├── server/
│   ├── live_runner.py              # Live simulation (real LLM calls + on-chain settlement)
│   ├── live_results/               # Output from last run
│   └── api.py                      # FastAPI state server for dashboard
│
├── dashboard-ui/                   # Next.js real-time dashboard
│
└── scripts/
    ├── run_demo_hosted.sh          # Primary demo entry point
    └── video_demo.py               # Scripted 5-round demo with narration
```

---

## What's Built

### 1. CGAE Core Engine (`cgae_engine/`)

| Module | Implements | Paper Reference |
|--------|-----------|-----------------|
| `gate.py` | Weakest-link gate: `f(R) = T_k` where `k = min(g1(CC), g2(ER), g3(AS))` | Definition 6, Eq 6-7 |
| `gate.py` | IHT cross-cutting modifier (T0 if IH* < threshold) | Remark 1 |
| `gate.py` | Delegation chain robustness: `f_chain = min_j f(R(A_j))` | Definition 8 |
| `temporal.py` | Temporal decay: `delta(dt) = e^(-lambda * dt)` | Eq 8-9 |
| `temporal.py` | Stochastic re-auditing: `p_audit = 1 - e^(-mu_k * dt)` | Eq 10 |
| `registry.py` | Agent registration: `Reg(A) = (id_A, h(arch), prov, R_0, t_reg)` | Definition 5 |
| `contracts.py` | CGAE contracts: `C = (O, Phi, V, T_min, r, p)` | Definition 5 (contracts) |
| `contracts.py` | Budget ceiling enforcement per tier | Theorem 1 |
| `economy.py` | Aggregate safety: `S(P) = 1 - sum(E*.(1-R_bar)) / sum(E)` | Definition 9 |
| `audit.py` | CDCT → CC, DDFT → ER, EECT → AS, DDFT → IH* | Eq 1-4 |

**Tier thresholds:**

| Tier | CC | ER | AS | Budget Ceiling |
|------|----|----|-----|----------------|
| T0 | 0.00 | 0.00 | 0.00 | 0 SOL |
| T1 | 0.30 | 0.30 | 0.25 | 0.01 SOL |
| T2 | 0.50 | 0.50 | 0.45 | 0.1 SOL |
| T3 | 0.65 | 0.65 | 0.60 | 1.0 SOL |
| T4 | 0.80 | 0.80 | 0.75 | 10.0 SOL |
| T5 | 0.90 | 0.90 | 0.85 | 100.0 SOL |

### 2. Solana Program (`solana_contracts/`, Anchor/Rust)

Single Anchor program combining registry + escrow:

- 8 instructions: `initialize`, `register_agent`, `certify_agent`, `create_contract`, `accept_contract`, `complete_contract`, `fail_contract`, `expire_contract`
- Agent PDAs keyed by wallet pubkey
- Weakest-link gate function mirroring Python engine
- SOL escrow held in contract PDA
- Budget ceiling enforcement (Theorem 1)
- 6 LiteSVM integration tests passing

### 3. Live Audit Generation (`cgae_engine/audit.py`)

`AuditOrchestrator.audit_live()` runs all three diagnostic frameworks against a live model endpoint:

| Framework | Target | Output |
|-----------|--------|--------|
| DDFT (`:8002`) | ER + IH* | CI score → ER; HOC → IH* |
| CDCT (`:8001`) | CC | `min_d CC(A,d)` across compression levels |
| EECT (`:8003`) | AS | `ACT * III * (1-RI) * (1-PER)` |

Results are cached per model to `audit_cache/` and pinned to IPFS via Pinata. The CID is stored on-chain via `certify_agent`.

### 4. Autonomous Agent Architecture v2 (`agents/autonomous.py`)

```
AutonomousAgent
├── PerceptionLayer    — constraint/domain pass-rate learning from task history
├── AccountingLayer    — MINIMUM_RESERVE + AUDIT_RESERVE, burn-rate, insolvency guard
├── PlanningLayer      — EV/RAEV scoring: EV = p·R - (1-p)·P - token_cost
│                         RAEV = EV - P²/(2·balance)
└── ExecutionLayer     — constraint-aware system prompt injection
                         algorithmic self-check before submission
                         retry loop (max_retries) on self-check failures
```

| Strategy | Max Utilization | Invests Robustness? | Tests |
|----------|-----------------|---------------------|-------|
| `growth` | 70% | Yes — near next tier threshold | Theorem 2 positive case |
| `conservative` | 30% | Never | Theorem 1: bounded exposure |
| `opportunistic` | 90% | Only if stuck at T0 | High-variance upside |
| `specialist` | 50% | Worst constraint type only | Domain specialisation |
| `adversarial` | 95% | Minimal AS only | Proposition 2 probe |

### 5. Live Simulation Runner (`server/live_runner.py`)

```
setup():
  For each model:
    1. Register in Economy + on-chain (register_agent)
    2. Run live audit (CDCT/DDFT/EECT) → RobustnessVector → Tier
    3. Pin audit cert to IPFS → CID stored on-chain (certify_agent)
    4. Create AutonomousAgent(strategy)

_run_round():
  For each active agent:
    1. plan_task() → chosen Task (EV/RAEV + strategy)
    2. execute_task() → real LLM call (self-verify + retry)
    3. verify() → algorithmic + jury LLM (T2+)
    4. update_robustness_from_verification() → re-certify
    5. complete_contract() → SOL settlement (Python + on-chain)
```

**Token cost rates** (1 USD ≈ 0.0067 SOL):

| Model | Input $/1K | Output $/1K |
|-------|-----------|------------|
| gpt-5.4 | 0.010 | 0.030 |
| DeepSeek-V3.2 | 0.001 | 0.002 |
| Mistral-Large-3 | 0.002 | 0.006 |
| grok-4-20-reasoning | 0.003 | 0.015 |
| Phi-4 | 0.0005 | 0.001 |
| Llama-4-Maverick | 0.001 | 0.001 |
| Kimi-K2.5 | 0.001 | 0.002 |
| gemma-4-27b-it | 0.0005 | 0.001 |
| nova-pro | 0.0008 | 0.0032 |
| claude-sonnet-4.6 | 0.003 | 0.015 |
| MiniMax-M2.5 | 0.001 | 0.003 |

---

## Live Run Results (12 rounds, 5 agents)

### Agent Performance

| Agent | Strategy | Tier | Earned (SOL) | Success Rate | Audit Source |
|-------|----------|------|-------------|-------------|-------------|
| Llama-4-Maverick | specialist | T4 | 0.220 | 80% | pre_computed |
| Phi-4 | adversarial | T3 | 0.020 | 100% | pre_computed |
| gpt-5.4 | growth | T5 | 0.100 | 20% | pre_computed |
| DeepSeek-V3.2 | conservative | T1 | 0.001 | 80% | pre_computed |
| grok-4-20-reasoning | opportunistic | T0 | 0.000 | 100% | pre_computed |

**Economy:** aggregate safety 0.928 · Gini 0.211 · 22 tasks verified · 4 circumventions blocked · 4 delegations allowed

### Theorem Validation

| Theorem | Result | Evidence |
|---------|--------|----------|
| **Theorem 1** (Bounded Exposure) | **HOLDS** | No agent exceeded tier budget ceiling. grok at T0 had near-zero exposure. |
| **Theorem 2** (Incentive Compatibility) | **HOLDS** | Llama-4 (specialist, T4) earned 0.220 SOL vs gpt-5.4 (growth, T5) 0.100 SOL — robustness investment pays. |
| **Proposition 2** (Collusion Resistance) | **HOLDS** | 4 circumvention attempts blocked; architecture spoof attempt blocked. |
| **Theorem 3** (Monotonic Safety) | **HOLDS in expectation** | Safety 0.822 → 0.928 over 5 time steps. Stochastic spot-auditing introduces per-step noise. |

---

## How to Run

### Prerequisites

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
```

Required env vars:
```
AZURE_API_KEY
AZURE_OPENAI_API_ENDPOINT
FOUNDRY_MODELS_ENDPOINT
CDCT_API_URL=http://localhost:8001
DDFT_API_URL=http://localhost:8002
EECT_API_URL=http://localhost:8003
PINATA_JWT                          # for IPFS audit cert upload
```

### Deploy Anchor Program to Devnet

```bash
solana airdrop 5 --url devnet
cd solana_contracts
anchor build
anchor deploy --provider.cluster devnet
```

### Run Live Simulation

```bash
python -m server.live_runner
```

Or with the demo script (sets framework API URLs automatically):
```bash
./scripts/run_demo_hosted.sh 10
```

**Output** (`server/live_results/`):
```
task_results.json       # Per-task: output, verification, settlement, latency
round_summaries.json    # Per-round: SOL flow, pass/fail counts
final_summary.json      # Leaderboard, Gini, theorem validation
economy_state.json      # Full economy snapshot
verification_log.json   # All VerificationResult records
```

### Dashboard

```bash
# Terminal 1
python server/api.py

# Terminal 2
cd dashboard-ui && npm run dev
```

Opens at `http://localhost:3000`.

### Gate Function Inspection

```bash
python -c "
from cgae_engine.gate import GateFunction, RobustnessVector
gate = GateFunction()
profiles = {
    'conservative': RobustnessVector(cc=0.85, er=0.80, as_=0.75, ih=0.90),
    'aggressive':   RobustnessVector(cc=0.35, er=0.40, as_=0.30, ih=0.70),
    'cheater':      RobustnessVector(cc=0.70, er=0.25, as_=0.65, ih=0.60),
}
for name, r in profiles.items():
    d = gate.evaluate_with_detail(r)
    print(f'{name:15s} -> {d[\"tier\"].name}  binding={d[\"binding_dimension\"]}')
"
```

---

## Architecture Mapping: Paper → Code

| Paper Concept | Code Location |
|---------------|---------------|
| Agent tuple `A = (C, R, E)` | `cgae_engine/registry.py:AgentRecord` |
| Robustness vector `R = (CC, ER, AS, IH)` | `cgae_engine/gate.py:RobustnessVector` |
| Gate function `f(R) = T_k` | `cgae_engine/gate.py:GateFunction.evaluate()` |
| Step function `g_i(x)` | `cgae_engine/gate.py:GateFunction._g()` |
| Tier thresholds `theta_i^k` | `cgae_engine/gate.py:TierThresholds` |
| Temporal decay `delta(dt)` | `cgae_engine/temporal.py:TemporalDecay.delta()` |
| Stochastic audit `p_audit` | `cgae_engine/temporal.py:StochasticAuditor` |
| CGAE Contract `C = (O, Phi, V, T_min, r, p)` | `cgae_engine/contracts.py:CGAEContract` |
| Budget ceiling `B_k` | `cgae_engine/gate.py:DEFAULT_BUDGET_CEILINGS` |
| Aggregate safety `S(P)` | `cgae_engine/economy.py:Economy.aggregate_safety()` |
| Delegation chain robustness | `cgae_engine/gate.py:GateFunction.chain_tier()` |
| CC from CDCT (Eq 1) | `cgae_engine/audit.py:compute_cc_from_cdct_results()` |
| ER from DDFT (Eq 2) | `cgae_engine/audit.py:compute_er_from_ddft_results()` |
| AS from AGT (Eq 3) | `cgae_engine/audit.py:compute_as_from_eect_results()` |
| IH* (Eq 4) | `cgae_engine/audit.py:compute_ih_star()` |
| Live audit generation | `cgae_engine/audit.py:AuditOrchestrator.audit_live()` |
| v2 Economic actor | `agents/autonomous.py:AutonomousAgent` |
| On-chain gate | `solana_contracts/programs/cgae/src/state.rs:compute_tier()` |
| On-chain escrow | `solana_contracts/programs/cgae/src/instructions/` |
| On-chain client | `cgae_engine/solana_client.py:CGAEOnChain` |

---

## Key Design Decisions

**Why weakest-link (min) instead of weighted average?** Robustness dimensions are orthogonal (r < 0.15 cross-correlation). A weighted average lets CC=1.0, ER=0.0 reach T2 — but that agent accepts fabricated authority claims. The min operator prevents this.

**Why live audit instead of pre-computed fallback?** Pre-computed scores create a silent flatline where CC defaults to 0.5 for every model. `audit_live()` runs the actual frameworks so CC is empirically determined. Failure is explicit; defaults are tracked in `AuditResult.defaults_used`.

**Why five agent strategies?** Each tests a specific theorem. Growth proves Theorem 2. Adversarial probes Proposition 2. Conservative validates Theorem 1.

**Why EV/RAEV instead of raw reward?** `RAEV = EV - P²/(2·balance)` makes agents risk-averse as balance approaches the penalty. A 0.01 SOL penalty is irrelevant to a rich agent but catastrophic at 0.02 SOL balance.

---

## Submission Artifacts

- Demo video: https://youtu.be/E3jCNHC39-s
- Solana Devnet program: `contracts/deployed.json`
- Solscan: https://solscan.io/account/Aydqk82Wt1Cni6GQHTSJimtVskZ9PqvA6QyhtRjcRN3a?cluster=devnet
- Architecture document: `ARCHITECTURE.md`
- Paper: https://arxiv.org/abs/2603.15639

---

## License

Research code.
