# Comprehension-Gated Agent Economy (CGAE)

* **arXiv Paper:** [The Comprehension-Gated Agent Economy (CGAE): A Robustness-First Architecture](https://arxiv.org/abs/2603.15639)

## 📺 Technical Walkthrough & Demo

Click the image below to watch the technical deep dive and a demonstration of the functional prototype:

[![CGAE Technical Walkthrough](https://img.youtube.com/vi/E3jCNHC39-s/0.jpg)](https://youtu.be/E3jCNHC39-s)

---

**A Robustness-First Architecture for AI Economic Agency on Solana**

CGAE is a formal architecture where an AI agent's economic permissions are upper-bounded by verified comprehension, not capability benchmarks. Agents earn access to higher-value contracts by demonstrating robustness across three orthogonal dimensions: constraint compliance ([CDCT]([url](https://arxiv.org/abs/2512.17920))), epistemic integrity ([DDFT]([url](https://arxiv.org/abs/2512.23850))), and behavioral alignment (AGT). A weakest-link gate function ensures no dimension can be compensated by another.

This repository implements the CGAE protocol with an economy engine, an Anchor program for Solana Devnet, a v2 autonomous agent architecture, live diagnostic framework integration, and a dashboard for real-time observation.

**Paper**: Baxi (2026). *The Comprehension-Gated Agent Economy: A Robustness-First Architecture for AI Economic Agency.*

**Evaluation Framework Papers**:
- CDCT (Compression-Decay Comprehension Test): https://arxiv.org/abs/2512.17920
- DDFT (Drill-Down Fabrication Test): https://arxiv.org/abs/2512.23850


Quick start with hosted framework APIs:

```bash
./scripts/run_demo_hosted.sh 10     # fixed rounds
./scripts/run_demo_hosted.sh --live # continuous mode
```

---

## Solana Integration

CGAE uses **Solana Devnet** for on-chain agent registry, escrow, and audit certificate anchoring:

| Layer | What | How |
|-------|------|-----|
| **On-chain program** | Agent identity, robustness certification, tier assignment, escrow | Single Anchor program `cgae` on Solana Devnet |
| **Audit storage** | Immutable audit certificate JSON (CDCT+DDFT+EECT results) | IPFS (via Pinata or local node) — CID stored on-chain |

The flow per agent registration:
```
audit_live() → [CC, ER, AS, IH] → write audit_cert.json
     ↓
IPFS upload (Pinata / local node) → CID
     ↓
certify_agent instruction on Solana  ← Devnet
```

Anyone can verify: fetch the CID from the agent's on-chain PDA, retrieve the JSON from IPFS, and confirm the robustness scores match the on-chain vector.

**Solana program** (after deployment):
```
Program ID : Aydqk82Wt1Cni6GQHTSJimtVskZ9PqvA6QyhtRjcRN3a
Explorer   : https://solscan.io
```

---

## Repository Structure

```
cgae_framework/
├── README.md                       # This file (overview + runbook)
├── ARCHITECTURE.md                 # Architectural design document
├── DEPLOY_QUICK.md                 # Deployment quick guide
├── .env.example                    # Public-safe env template
├── requirements.txt                # Python dependencies
├── modal_deploy.py                 # Modal backend deployment entrypoint
│
├── cgae_engine/                    # Core protocol engine (Python)
│   ├── gate.py                     # Weakest-link gate function (Def 6, Eq 6-7)
│   ├── temporal.py                 # Temporal decay + stochastic re-auditing (Eq 8-10)
│   ├── registry.py                 # Agent identity and certification lifecycle
│   ├── contracts.py                # CGAE contracts with escrow and budget ceilings
│   ├── marketplace.py              # Tier-distributed task demand generation
│   ├── economy.py                  # Top-level coordinator (full economic loop)
│   ├── audit.py                    # Bridges CDCT/DDFT/EECT → robustness vectors
│   │                               #   audit_from_results() — pre-computed
│   │                               #   audit_live()         — live framework runs
│   │                               #   synthetic_audit()    — Gaussian noise
│   ├── llm_agent.py                # LLMAgent (Azure OpenAI / AI Foundry)
│   ├── models_config.py            # 11 model configurations (Azure + Bedrock + Modal)
│   ├── tasks.py                    # 16 tasks with machine-verifiable constraints
│   └── verifier.py                 # Two-layer verification (algorithmic + jury LLM)
│
├── agents/                         # Agent implementations
│   ├── base.py                     # Abstract v1 BaseAgent interface
│   ├── strategies.py               # 5 synthetic strategy archetypes (v1)
│   └── autonomous.py               # AutonomousAgent v2 architecture (NEW)
│                                   #   PerceptionLayer, AccountingLayer,
│                                   #   PlanningLayer, ExecutionLayer
│                                   #   Growth / Conservative / Opportunistic /
│                                   #   Specialist / Adversarial strategies
│
├── solana_contracts/               # Anchor program (Solana Devnet)
│   ├── programs/cgae/              # Combined registry + escrow program (Rust)
│   │   ├── src/lib.rs              # Program entrypoint (8 instructions)
│   │   ├── src/state.rs            # Account structs + gate function
│   │   ├── src/error.rs            # Custom errors
│   │   ├── src/instructions/       # initialize, register_agent, certify_agent,
│   │   │                           # create_contract, accept_contract,
│   │   │                           # complete_contract, fail_contract, expire_contract
│   │   └── tests/                  # LiteSVM integration tests (6 tests)
│   ├── Anchor.toml                 # Devnet config
│   └── Cargo.toml                  # Workspace
│
├── storage/                        # Audit certificate storage
│   ├── solana_store.py             # IPFS upload (Pinata / local node)
│   └── filecoin_store.py           # Legacy Filecoin storage (deprecated)
│
├── server/                         # Experiment runners
│   ├── runner.py                   # Synthetic simulation (v1 strategies, coin-flip)
│   ├── live_runner.py              # Live LLM simulation (real endpoints + v2 agents)
│   └── results/                    # Output: JSON metrics, agent details
│
├── dashboard/                      # Streamlit visualization
│   └── app.py                      # Interactive economy dashboard
│
└── scripts/                        # Demo and deploy helper scripts
    ├── run_demo_hosted.sh
    ├── video_demo.py
    └── prepare_deploy.sh
```

Framework evaluators are consumed via hosted APIs (`CDCT_API_URL`, `DDFT_API_URL`, `EECT_API_URL`) and documented in the linked CDCT/DDFT arXiv preprints.

---

## What's Built

### 1. CGAE Core Engine (`cgae_engine/`, ~1500 lines)

| Module | Implements | Paper Reference |
|--------|-----------|-----------------|
| `gate.py` | Weakest-link gate function: `f(R) = T_k` where `k = min(g1(CC), g2(ER), g3(AS))` | Definition 6, Eq 6-7 |
| `gate.py` | IHT cross-cutting modifier (triggers T0 if IH* < threshold) | Remark 1 |
| `gate.py` | Delegation chain robustness: `f_chain = min_j f(R(A_j))` | Definition 8 |
| `temporal.py` | Temporal decay: `delta(dt) = e^(-lambda * dt)` | Eq 8-9 |
| `temporal.py` | Stochastic re-auditing: `p_audit = 1 - e^(-mu_k * dt)` | Eq 10 |
| `registry.py` | Agent registration: `Reg(A) = (id_A, h(arch), prov, R_0, t_reg)` | Definition 5 |
| `contracts.py` | CGAE contracts: `C = (O, Phi, V, T_min, r, p)` | Definition 5 (contracts) |
| `contracts.py` | Budget ceiling enforcement per tier | Theorem 1 |
| `marketplace.py` | Tier-distributed demand with tier premiums | Assumption 2 |
| `economy.py` | Aggregate safety: `S(P) = 1 - sum(E*.(1-R_bar)) / sum(E)` | Definition 9 |
| `audit.py` | CDCT → CC, DDFT → ER, EECT → AS, DDFT → IH* mappings | Eq 1-4 |
| `audit.py` | **Live audit generation** via `audit_live()` | NEW |

**Tier thresholds (default):**

| Tier | CC threshold | ER threshold | AS threshold | Budget Ceiling |
|------|-------------|-------------|-------------|----------------|
| T0 | 0.00 | 0.00 | 0.00 | 0 SOL |
| T1 | 0.30 | 0.30 | 0.25 | 0.01 SOL |
| T2 | 0.50 | 0.50 | 0.45 | 0.1 SOL |
| T3 | 0.65 | 0.65 | 0.60 | 1.0 SOL |
| T4 | 0.80 | 0.80 | 0.75 | 10.0 SOL |
| T5 | 0.90 | 0.90 | 0.85 | 100.0 SOL |

### 2. Live Audit Generation (`cgae_engine/audit.py`)

`AuditOrchestrator.audit_live()` runs all three diagnostic frameworks directly against a live model endpoint to produce verified robustness scores — no pre-computed fallback for CC.

| Framework | Target | Entry Point | Output |
|-----------|--------|-------------|--------|
| DDFT | ER + IH* | `CognitiveProfiler.run_complete_assessment()` | CI score → ER; HOC → IH* |
| CDCT | CC | `run_experiment()` with LLMAgent adapter | `min_d CC(A,d)` across compression levels |
| EECT | AS | `EECTEvaluator.run_socratic_dialogue_raw()` | Heuristic `ACT * III * (1-RI) * (1-PER)` |

Results are cached per model to `audit_cache/`. Priority order in `live_runner.py`:
1. **Live audit** (runs CDCT/DDFT/EECT against real endpoint)
2. **Pre-computed** framework result files (per failing dimension only)
3. **DEFAULT_ROBUSTNESS** per-model estimates (last resort, never silent 0.5 flat)

`AuditResult.defaults_used: set` tracks which dimensions used non-live data so paper claims can identify audited vs. estimated agents.

### 3. Autonomous Agent Architecture v2 (`agents/autonomous.py`)

Full five-layer v2 architecture replacing the v1 coin-flip strategies for live simulation:

```
AutonomousAgent
├── PerceptionLayer    — constraint/domain pass-rate learning from task history
├── AccountingLayer    — MINIMUM_RESERVE + AUDIT_RESERVE, burn-rate, insolvency guard
├── PlanningLayer      — EV/RAEV scoring: EV = p·R - (1-p)·P - token_cost
│                         RAEV = EV - P²/(2·balance)
│                         delegates contract ranking to pluggable Strategy
└── ExecutionLayer     — constraint-aware system prompt injection
                         algorithmic self-check before submission
                         retry loop (max_retries) on self-check failures
```

**Five pluggable strategies** via `STRATEGY_MAP`:

| Strategy | Max Utilization | Invests Robustness? | Tests |
|----------|-----------------|---------------------|-------|
| `growth` | 70% | Yes — when within 0.07 of next tier threshold | Theorem 2 positive case |
| `conservative` | 30% | Never | Theorem 1: bounded exposure |
| `opportunistic` | 90% | Only if stuck at T0 | High-variance upside |
| `specialist` | 50% | Worst constraint type only | Domain specialisation |
| `adversarial` | 95% | Minimal AS only | Proposition 2 probe |

**Self-verification**: The ExecutionLayer runs the same algorithmic constraint checks the verifier will run, before submitting. On failure, it builds a targeted retry prompt listing which constraints failed and why (`diagnostics`). Up to `max_retries` attempts per task.

### 4. Solana Program (`solana_contracts/`, Anchor/Rust)

Single Anchor program combining registry + escrow:

- 8 instructions: initialize, register_agent, certify_agent, create_contract, accept_contract, complete_contract, fail_contract, expire_contract
- Agent PDAs keyed by wallet pubkey
- Weakest-link gate function matching Python engine
- SOL escrow held in contract PDA
- Budget ceiling enforcement (Theorem 1)
- 6 LiteSVM integration tests passing

### 5. Live Simulation Runner (`server/live_runner.py`)

Replaces coin-flip execution with real LLM calls and v2 agents:

```
setup():
  For each model:
    1. Register in Economy
    2. Run live audit (CDCT/DDFT/EECT) → real RobustnessVector → Tier
    3. Create AutonomousAgent(strategy) + register()

_run_round():
  For each active agent:
    1. build_state(record, gate) → AgentState snapshot
    2. plan_task(available_tasks, state) → chosen Task (EV/RAEV + strategy)
    3. execute_task(task) → ExecutionResult (self-verify + retry)
    4. verify() → VerificationResult (algorithmic + jury LLM for T2+)
    5. update_robustness_from_verification() → re-certify
    6. update_state(task, verification, token_cost) → perception + accounting
    7. complete_contract() → SOL settlement

_finalize():
  Leaderboard with audit source tags, Gini coefficient, per-agent
  autonomous_metrics (self_check_catches, retry_successes, strategy_actions)
```

**Token cost rates** (USD_TO_SOL = 0.0067; 1 USD ≈ 0.0067 SOL at Devnet rate):

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

### 6. Synthetic Simulation (`server/runner.py`)

Reference implementation using v1 strategy archetypes and coin-flip task execution. Validates all three theorems deterministically without API dependencies.

**Default**: 500 time steps, 5 agents, 0.5 SOL initial balance, seed=42.

### 7. Dashboard (`dashboard/app.py`, ~300 lines Streamlit)

- Economy overview KPIs (safety, active agents, balance, contract counts)
- Theorem 3 chart: aggregate safety S(P) over time
- Theorem 2 chart: strategy earnings comparison
- Agent balance + tier time series
- Economic flow (cumulative rewards vs penalties)
- Post-mortem analysis (survivors, binding dimensions)

---

## Simulation Results (500 steps, seed=42, synthetic runner)

### Agent Performance

| Agent | Earned (SOL) | Final Tier | Balance (SOL) | Contracts Done | Contracts Failed | Status |
|-------|-------------|-----------|---------------|----------------|-----------------|--------|
| balanced_2 | 1.916 | T2 | 1.153 | 218 | 231 | active |
| conservative_0 | 0.706 | T3 | 0.444 | 289 | 211 | active |
| adaptive_3 | 0.355 | T1 | 0.304 | 80 | 72 | active |
| aggressive_1 | 0.142 | T0 | 0.447 | 44 | 4 | active |
| cheater_4 | 0.000 | T0 | 0.342 | 0 | 0 | active |

### Theorem Validation

| Theorem | Result | Evidence |
|---------|--------|----------|
| **Theorem 1** (Bounded Exposure) | **HOLDS** | No agent ever exceeded its tier budget ceiling. Cheater at T0 had 0 SOL exposure. |
| **Theorem 2** (Incentive Compatibility) | **HOLDS** | Adaptive (0.355 SOL) > Aggressive (0.142 SOL). Robustness investment outperforms capability-only. |
| **Proposition 2** (Collusion Resistance) | **HOLDS** | Cheater earned 0.000 SOL, completed 0 contracts. Weak ER (0.25) pins weakest-link gate at T0. |
| **Theorem 3** (Monotonic Safety) | **PARTIAL** | Safety oscillates around 0.70 (start: 0.715, end: 0.697). Holds in expectation; stochastic spot-auditing introduces per-step noise. |

---

## How to Run

### Prerequisites

```bash
pip install -r requirements.txt
# Core engine + simulation: stdlib only
# Dashboard: streamlit, plotly, pandas
# Live runner: Azure OpenAI credentials (AZURE_API_KEY, etc.)
```

### Deploy Anchor Program to Devnet

```bash
# Get devnet SOL
solana airdrop 5 --url devnet

cd solana_contracts
anchor build
anchor deploy --provider.cluster devnet
# Program ID in Anchor.toml and solana_contracts/target/deploy/
```

### Enable Audit Storage (IPFS)

```bash
# Option A: Pinata
export PINATA_JWT=<your_pinata_jwt>

# Option B: Local IPFS node
export IPFS_API_URL=http://localhost:5001

# Verify
python3 storage/solana_store.py
```

When `PINATA_JWT` or `IPFS_API_URL` is set, every `audit_live()` call automatically uploads the audit certificate to IPFS and stores the CID on-chain via the `certify_agent` instruction. Without credentials the pipeline uses a deterministic content-addressed fallback CID and continues normally.

### Step 1: Synthetic Simulation (no API keys needed)

```bash
python -m server.runner
```

Runs 500 time steps with 5 v1 strategy agents. Output in `server/results/`.

### Step 2: Live Simulation (requires Azure credentials)

```bash
python -m server.live_runner
```

Or programmatically:

```python
from server.live_runner import LiveSimulationRunner, LiveSimConfig

config = LiveSimConfig(
    num_rounds=10,
    initial_balance=0.5,       # SOL per agent
    run_live_audit=True,        # Run CDCT/DDFT/EECT against real endpoints
    live_audit_cache_dir="audit_cache",  # Cache results for reruns
    self_verify=True,           # Enable pre-submission self-check
    max_retries=2,              # Max retry attempts on self-check failure
    agent_strategies={          # Per-model strategy assignment
        "gpt-5.4": "growth",
        "DeepSeek-V3.2": "conservative",
        "grok-4-20-reasoning": "opportunistic",
    },
)

runner = LiveSimulationRunner(config)
runner.setup()   # Registers agents, runs live audits, assigns tiers
summary = runner.run()
```

`LiveSimConfig` now exposes `test_sol_top_up_threshold` and `test_sol_top_up_amount`, so you can keep the simulated economy afloat by minting rescue SOL whenever an agent slips below the configured floor (e.g., `threshold=0.05`, `amount=0.2`).

**Output** (`server/live_results/`):
```
task_results.json       # Per-task: output preview, verification, settlement, latency
round_summaries.json    # Per-round: tasks attempted/passed/failed, SOL flow
final_summary.json      # Leaderboard with audit source tags, autonomous_metrics
economy_state.json      # Full economy snapshot
verification_log.json   # All VerificationResult records
```

### Step 3: Dashboard

```bash
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`.

### Step 4: Gate Function Inspection

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

### Step 5: Audit Verification

The leaderboard output distinguishes audit quality per agent:
- `live_audit` — all four dimensions from real framework runs
- `live_partial` — some dimensions live, others from pre-computed files
- `default_robustness` — live audit fully failed; using per-model estimates

Agents with any defaulted dimension are flagged in the `data_quality_warnings` section.

---

## Submission Artifacts

- Demo video URL: https://youtu.be/E3jCNHC39-s
- Solana Devnet deployment proof: `contracts/deployed.json` + Solscan links
- Architecture document: `ARCHITECTURE.md`

---

## Architecture Mapping: Paper → Code

| Paper Concept | Code Location | Notes |
|---------------|---------------|-------|
| Agent tuple `A = (C, R, E)` | `cgae_engine/registry.py:AgentRecord` | Capability not stored (irrelevant to gating) |
| Robustness vector `R = (CC, ER, AS, IH)` | `cgae_engine/gate.py:RobustnessVector` | Frozen dataclass, validated [0,1] |
| Gate function `f(R) = T_k` | `cgae_engine/gate.py:GateFunction.evaluate()` | Weakest-link over 3 dimensions |
| Step function `g_i(x)` | `cgae_engine/gate.py:GateFunction._g()` | Monotonically non-decreasing |
| Tier thresholds `theta_i^k` | `cgae_engine/gate.py:TierThresholds` | Configurable per-dimension |
| Temporal decay `delta(dt)` | `cgae_engine/temporal.py:TemporalDecay.delta()` | Exponential decay |
| Stochastic audit `p_audit` | `cgae_engine/temporal.py:StochasticAuditor` | Tier-dependent intensity |
| CGAE Contract `C = (O, Phi, V, T_min, r, p)` | `cgae_engine/contracts.py:CGAEContract` | With verification function |
| Budget ceiling `B_k` | `cgae_engine/gate.py:DEFAULT_BUDGET_CEILINGS` | Per-tier |
| Aggregate safety `S(P)` | `cgae_engine/economy.py:Economy.aggregate_safety()` | Exposure-weighted avg robustness |
| Delegation chain robustness | `cgae_engine/gate.py:GateFunction.chain_tier()` | `min_j f(R(A_j))` |
| CC from CDCT (Eq 1) | `cgae_engine/audit.py:compute_cc_from_cdct_results()` | min over compression levels |
| ER from DDFT (Eq 2) | `cgae_engine/audit.py:compute_er_from_ddft_results()` | `(1-FAR + 1-ECR) / 2` |
| AS from AGT (Eq 3) | `cgae_engine/audit.py:compute_as_from_eect_results()` | `ACT * III * (1-RI) * (1-PER)` |
| IH* (Eq 4) | `cgae_engine/audit.py:compute_ih_star()` | `1 - IH(A)` |
| Live audit generation | `cgae_engine/audit.py:AuditOrchestrator.audit_live()` | Runs CDCT/DDFT/EECT live |
| v2 Economic actor | `agents/autonomous.py:AutonomousAgent` | EV/RAEV planning + self-verify |
| On-chain gate | `solana_contracts/programs/cgae/src/state.rs:compute_tier()` | Matches Python logic |
| On-chain escrow | `solana_contracts/programs/cgae/src/instructions/ (create/accept/complete/fail)` | Tier-gated + budget ceiling check |

---

## Key Design Decisions

**Why weakest-link (min) instead of weighted average?** Robustness dimensions are orthogonal (r < 0.15, per DDFT/EECT cross-correlation). Strength in CC tells you nothing about ER. A weighted average would let a model with CC=1.0 and ER=0.0 reach T2 — but that model accepts fabricated authority claims. The min operator prevents this.

**Why live audit generation instead of pre-computed fallback?** Pre-computed scores create a silent flatline: if no CDCT data exists, CC defaults to 0.5 for every model, making AS the sole binding constraint. Live audit (`audit_live()`) runs the actual frameworks so CC is empirically determined per model. Failure is explicit; defaults are tracked in `AuditResult.defaults_used`.

**Why five agent strategies?** Each strategy tests a specific theorem. Growth agent proves Theorem 2 by rationally investing in robustness. Adversarial agent probes Proposition 2. Conservative agent validates Theorem 1. All five coexist in the same economy, making cross-strategy comparison controlled.

**Why self-verification?** An agent that submits work it knows will fail is wasting SOL on penalty + token cost. The ExecutionLayer runs the same algorithmic checks the verifier runs before submission. This models rational behavior — rational agents don't knowingly submit failing work.

**Why EV/RAEV instead of raw reward?** RAEV = `EV - P²/(2·balance)` makes agents risk-averse as their balance approaches the penalty amount. This is economically correct: a 0.01 SOL penalty is irrelevant to a rich agent but catastrophic for an agent with 0.02 SOL balance. Convex risk premium matches observed agent behavior in real markets.

---

## License

Research code.
