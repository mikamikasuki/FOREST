# FOREST

### Persistent Research State for Long-Horizon Autonomous Science

**LLMs propose. Evidence decides.**

FOREST is a goal-driven research runtime for long-horizon scientific work. It gives language-model agents persistent research state, executable experiments, independent verification, and explicit control over branching, pruning, recovery, and claim formation.

A frontier model can be highly capable at an individual step and still lose a research trajectory over hundreds of steps. FOREST is designed around that distinction.

---

## Quick Start

### macOS

Launch with:

```text
Start Forest.command
```

or run in development mode:

```bash
npm install
npm run doctor
npm run dev
```

Before a serious research run:

```bash
npm run verify
```

Common commands:

```bash
npm run dev
npm run build
npm run test
npm run test:e2e
npm run benchmark
npm run demo
```

### Execution Modes

- **LIVE LLM** — full autonomous research with a configured model provider and real experiment execution.
- **LOCAL BENCHMARK** — controlled workloads for testing, ablations, and architecture evaluation.
- **VERIFIED REPLAY** — reconstructs persisted runs without repeating model calls or experiments.

### Runtime Notes

- Prefer a strong remote model API when the local GPU is needed for experiments. Local inference and long KV caches can compete directly with training, simulation, or vision workloads and cause OOM failures.
- Execute model-generated code in an isolated workspace or container when possible.
- Bound wall time, API spend, process count, disk usage, GPU allocation, and retries before long runs.
- Recompute important results from raw artifacts rather than validating only another agent's summary.
- Record dependency versions, dataset versions, model IDs, seeds, and runtime configuration for experiments supporting scientific claims.
- Keep provider credentials and machine-specific configuration outside the repository.

---

## Submission Workflow

Scientific projects default to a complete submission and `full_paper`, with real experiments, accepted-paper comparison and a review-and-repair loop. A pilot or exhausted budget leaves the full delivery incomplete. See the [submission workflow](docs/PUBLICATION_DELIVERY.md) and [16-paper official reference corpus](docs/PUBLICATION_REFERENCE_CORPUS.md). [Paper authoring](docs/PAPER_AUTHORING.md) explains independent visual selection, manuscript placement and manual insertion from Figure Studio.

---

## Why FOREST

Modern language models can formulate hypotheses, write code, inspect literature, run tools, interpret results, and draft technical papers. The harder problem begins when these capabilities are composed into a research process that unfolds over hundreds of dependent decisions.

Long-horizon research creates a compounding state-management problem. As the trajectory grows, decision-relevant evidence is diluted by accumulated context, unverified interpretations propagate into later decisions, and locally reasonable actions can gradually shift the process away from its original objective. Summarization may discard failure-relevant information, competing hypotheses can remain unresolved, and self-critique can reproduce correlated errors when it relies on the same underlying assumptions.

Existing work already exposes several parts of this failure surface. *Lost in the Middle* and RULER show that usable long-context reasoning can degrade well before the nominal context window is exhausted [1,2]. Recent work on long-horizon search identifies **context rot** and increasing **premature termination** as trajectories grow [3]. Goal-drift evaluations show that autonomous agents can gradually deviate from their assigned objectives [4]. Intrinsic self-correction is also unreliable without external feedback: reconsidering a previous answer can preserve or even amplify the original error [5].

FOREST frames autonomous research as a **state-control problem**: the central challenge is not only choosing a good next action, but maintaining a scientifically valid state from which that action is chosen.

> **Stronger models can reduce local reasoning error. FOREST targets the mechanisms through which local errors accumulate, propagate, and eventually distort the global research trajectory.**

---

## Long-Horizon Research Drift

We use **Long-Horizon Research Drift** to describe the progressive divergence between the research state required by the original objective and the state that actually drives the agent.

Let:

- $g$ denote the research goal;
- $z_t$ denote the latent scientific state after step $t$;
- $\hat{z}_t$ denote the scientific state represented to the agent;
- $a_t$ denote the next research action.

The agent selects:

```math
a_t \sim \pi_\theta(a \mid \hat{z}_t, g)
```

A conventional transcript-driven agent maintains an accumulated history:

```math
H_t = H_{t-1} \oplus (a_t, o_{t+1})
```

As the trajectory grows, an increasing fraction of the working state is encoded in accumulated text rather than explicit scientific structure.

This creates four recurring sources of drift:

| Failure Mode | Effect |
|---|---|
| **Context dilution** | Decision-relevant evidence occupies a decreasing fraction of active context. |
| **Verification debt** | Unchecked intermediate claims become assumptions for later claims. |
| **Goal displacement** | Locally attractive tasks gradually replace the original research objective. |
| **Premature conservative convergence** | Accumulated uncertainty becomes a reason to stop exploring even when discriminating experiments remain available. |

The final failure mode is particularly important in scientific work.

Uncertainty is a property of the current evidence state. It should not automatically become a terminal action.

Statements such as *insufficient evidence* or *more research is required* have limited scientific value unless the system can identify what evidence is missing and which experiment could change the decision.

FOREST keeps uncertainty executable: an unresolved claim remains connected to the experiments capable of resolving it.

---

## Why Small Errors Become Large Failures

Let $\epsilon_t$ denote the conditional probability of a trajectory-breaking error at step $t$, given that no previous step has already broken the trajectory:

```math
\epsilon_t = P(D_t = 1 \mid D_1 = 0, \ldots, D_{t-1} = 0)
```

The probability that a research trajectory remains valid through $T$ consequential steps is then:

```math
P_{\mathrm{valid}}(T) = \prod_{t=1}^{T}(1-\epsilon_t)
```

If the local failure probability is bounded below by a persistent rate $\epsilon > 0$:

```math
\epsilon_t \ge \epsilon > 0
```

then:

```math
P_{\mathrm{valid}}(T) \le (1-\epsilon)^T \approx e^{-\epsilon T}
```

Long-horizon reliability is therefore highly sensitive to persistent local error, even when the per-step error rate is small.

A stronger base model can reduce $\epsilon_t$. It does not remove the compounding effect of trajectory length.

FOREST changes the transition process itself.

---

## Research State as a Graph

FOREST externalizes scientific state into a persistent typed graph:

```math
G_t = (V_t, E_t)
```

Typical node types include:

```text
Goal
Question
Hypothesis
Method
Experiment
Result
Evidence
Claim
Failure
Decision
Artifact
```

Typical relations include:

```text
tests
supports
contradicts
depends_on
derived_from
supersedes
motivates
blocks
```

Each scientific claim can retain the evidence supporting it, the experiments that tested it, its dependencies, and unresolved competing hypotheses.

Nodes also carry explicit lifecycle state:

```text
proposed
scheduled
running
tested
supported
contradicted
unresolved
superseded
pruned
verified
```

The complete graph can grow throughout a long research program without forcing the model to consume the entire state at every step.

FOREST instead constructs an **active frontier**:

```math
F_t = R_K(G_t, g)
```

Here, $R_K$ denotes a retrieval policy that selects the goal, relevant hypotheses, unresolved evidence, dependencies, current artifacts, and immediate decision context.

Persistent scientific state may grow for hours. Active reasoning context does not need to grow at the same rate.

---

## Goal Contract

Every FOREST run begins with a **Goal Contract**:

```math
C = (g, S, K, B)
```

where:

- $g$ is the research objective;
- $S$ contains success and falsification criteria;
- $K$ contains operational and scientific constraints;
- $B$ contains compute, time, model, and experiment budgets.

The Goal Contract remains outside the rolling conversation history and participates directly in planning.

A planner can be interpreted through an action utility:

```math
U(a) = \lambda_g R_g(a) + \lambda_i I_G(a) - \lambda_c C(a) - \lambda_r R(a)
```

where:

- $R_g(a)$ measures relevance to the Goal Contract;
- $I_G(a)$ measures expected information gain;
- $C(a)$ measures resource cost;
- $R(a)$ measures execution or scientific risk.

The exact policy may vary between workflows. The invariant is that the next action is selected from explicit scientific state rather than narrative momentum alone.

---

## Zero-Trust Research

Agent output is treated as a proposal until an external consequence supports it.

FOREST separates the research loop into distinct roles:

```text
Planner
   ↓
Builder / Researcher
   ↓
Executor
   ↓
Tester
   ↓
Challenger
   ↓
Validator
   ↓
Evidence Gate
```

A generated interpretation cannot establish its own correctness.

Code is executed. Metrics are recomputed. Assertions are checked against artifacts. Competing explanations can be challenged. Failed operations remain visible in the research state.

A claim can therefore be rejected even when the prose describing it is persuasive.

> **LLMs propose. Evidence decides.**

---

## Independent Verification

Self-reflection is useful for proposing possible mistakes. Verification should come from a different information path whenever practical.

Suppose a harmful local error occurs with probability $\epsilon_t$. Let $d_t$ be the probability that independent verification detects it, and let $r_t$ be the probability that the recovery path successfully repairs the detected error.

Under the simplifying assumption that successful repair prevents the local failure from propagating, the effective hazard becomes:

```math
\epsilon'_t = \epsilon_t(1-d_t r_t)
```

The corresponding trajectory survival probability becomes:

```math
P_{\mathrm{FOREST}} = \prod_t \left(1-\epsilon_t(1-d_t r_t)\right)
```

Whenever:

```math
d_t r_t > 0
```

independent verification reduces the effective probability of propagated error under this model.

This is a mechanism-level argument rather than an empirical performance claim. Its components can be measured directly through controlled evaluation: local error rate, detection rate, repair rate, trajectory drift, compute cost, and final-task utility.

---

## Branch, Challenge, Prune, Recover

Scientific search rarely follows a single monotonic path.

FOREST allows the research structure to change as evidence arrives:

```text
                 ┌── Hypothesis A ── Experiment ── Contradicted ── Prune
Goal ─ Question ─┤
                 ├── Hypothesis B ── Experiment ── Supported ── Expand
                 │
                 └── Hypothesis C ── Failure ── Repair ── Retest
```

A graph rewrite may:

```text
branch
prune
merge
split
re-parent
supersede
reactivate
recover
```

Pruning is a resource-allocation operation. Once a research direction is contradicted by evidence, it should stop consuming model tokens, GPU time, and experimental budget. Its evidence remains available so the same failed direction does not need to be rediscovered later.

FOREST also distinguishes **experiment failure** from **hypothesis falsification**. A failed run says that an experiment did not produce usable evidence; a falsified hypothesis says that valid evidence contradicted a scientific claim.

---

## Premature Conservative Convergence

Preference-tuned language models can exhibit systematic response biases, including sycophancy [6]. Intrinsic self-correction can fail without reliable external signals [5]. Long-horizon search research further suggests that models can terminate early or produce uncertain incorrect answers as context grows [3].

FOREST treats premature convergence as a control-flow problem.

An agent may express uncertainty at any stage. Terminating a branch requires a concrete condition:

- the hypothesis is contradicted by evidence;
- the relevant claim is sufficiently resolved;
- a predefined feasibility or budget boundary has been reached;
- available actions have negligible expected information value;
- the Goal Contract has been satisfied.

Otherwise, uncertainty remains an unresolved state and the planner searches for a discriminating action.

This preserves scientific calibration while preventing generic caution from silently becoming a stopping policy.

---

## Architecture

```text
                         ┌────────────────────┐
                         │    Goal Contract   │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │   Research Graph   │◄─────────────┐
                         └─────────┬──────────┘              │
                                   │                         │
                            Active Frontier                  │
                                   │                         │
                                   ▼                         │
                         ┌────────────────────┐              │
                         │      Planner       │              │
                         └─────────┬──────────┘              │
                                   │                         │
                                   ▼                         │
                         ┌────────────────────┐              │
                         │      Executor      │              │
                         └─────────┬──────────┘              │
                                   │                         │
                            Real Artifacts                   │
                                   │                         │
                                   ▼                         │
                     ┌──────────────────────────┐            │
                     │ Tester / Challenger /    │            │
                     │ Independent Validator    │            │
                     └────────────┬─────────────┘            │
                                  │                          │
                                  ▼                          │
                         ┌────────────────────┐              │
                         │   Evidence Gate    │              │
                         └─────────┬──────────┘              │
                                   │                         │
                                   ▼                         │
                         ┌────────────────────┐              │
                         │   Graph Rewriter   │──────────────┘
                         └─────────┬──────────┘
                                   │
                         branch / prune / merge
                         repair / supersede
                                   │
                                   ▼
                         ┌────────────────────┐
                         │ Verified Research  │
                         │ HTML / PDF / Data  │
                         └────────────────────┘
```

---

## Research Lifecycle

A typical FOREST run follows:

```text
Goal Contract
→ research decomposition
→ competing hypotheses
→ executable experiments
→ evidence collection
→ challenge and verification
→ branch / prune / recovery
→ holdout confirmation
→ claim construction
→ evidence-linked manuscript
→ HTML / PDF
```

The manuscript sits downstream of the evidence graph, allowing scientific claims to remain traceable to the experiments and artifacts that support them.

---

## Interface

FOREST exposes the evolving research state directly.

The workspace includes:

- Goal Contract and run status;
- editable Research Graph;
- hypotheses and competing branches;
- experiment state;
- evidence and claim relationships;
- challenge and verification state;
- metrics and figures;
- event stream;
- paper workspace;
- run replay.

Research paths can be inspected and edited while preserving the underlying evidence structure.

---

## Implementation

| Layer | Stack |
|---|---|
| UI | React, TypeScript, Vite, Tailwind |
| Research Graph | `@xyflow/react` |
| Metrics / Figures | Recharts, SVG |
| Runtime | Node.js, TypeScript, Express |
| Experiment Execution | `worker_threads` and local processes |
| Validation | Zod |
| Streaming | Server-Sent Events |
| Persistence | Filesystem-backed `runs/<runId>/` artifacts |
| Paper Export | Playwright |
| Tests | Vitest, Playwright |

The local server binds to `127.0.0.1` by default.

---

## Relation to Existing Work

FOREST builds on several lines of research.

### Long-Context Reliability

*Lost in the Middle* [1], RULER [2], and recent work on context rot [3] show that nominal context length can overstate reliable long-context reasoning.

FOREST stores persistent research state outside the prompt and constructs a decision-specific active frontier.

### Goal Stability

Goal-drift evaluations [4] motivate explicit goal anchoring across long autonomous trajectories.

FOREST stores the Goal Contract as persistent runtime state and uses it throughout planning.

### Feedback and Self-Correction

Reflexion demonstrates the value of feedback across agent attempts [7], while later work shows that intrinsic self-correction without reliable external signals can fail [5].

FOREST therefore prioritizes executable and independently recomputed feedback.

### Search Over Reasoning Trajectories

Tree of Thoughts and Language Agent Tree Search demonstrate the value of branching, lookahead, backtracking, and environment feedback [8].

AIDE applies structured search to iterative machine-learning engineering. AI Scientist-v2 extends agentic tree search toward automated scientific discovery [9,10].

FOREST focuses on the **lifecycle of scientific state across the entire research program**.

Its graph can express dependencies between branches, shared evidence, contradictions, failed experiments, supersession, recovery, and claims that reuse evidence generated elsewhere in the graph. Search policy and scientific provenance therefore share the same persistent state.

---

## Design Principle

A long-running research agent should not depend on its transcript alone to remember the project.

The model performs stochastic research operations. The runtime carries scientific state. The graph carries dependencies. Artifacts carry evidence. Verification controls promotion from observation to claim. The Goal Contract controls direction.

That separation is the core of FOREST.

---

## References

**[1]** Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M., Petroni, F., & Liang, P.  
*Lost in the Middle: How Language Models Use Long Contexts.*  
Transactions of the Association for Computational Linguistics, 2024.  
DOI: `10.1162/tacl_a_00638`

**[2]** Hsieh, C.-P., Sun, S., Kriman, S., Acharya, S., Rekesh, D., Jia, F., Zhang, Y., & Ginsburg, B.  
*RULER: What's the Real Context Size of Your Long-Context Language Models?*  
arXiv:`2404.06654`, 2024.

**[3]** Xia, S., Wang, Y., Huang, Z., & Liu, P.  
*Diagnosing and Mitigating Context Rot in Long-horizon Search.*  
arXiv:`2606.29718`, 2026.

**[4]** Arike, R., Donoway, E., Bartsch, H., & Hobbhahn, M.  
*Evaluating Goal Drift in Language Model Agents.*  
AAAI/ACM Conference on AI, Ethics, and Society, 2025.  
DOI: `10.1609/aies.v8i1.36541`

**[5]** Huang, J., Chen, X., Mishra, S., Zheng, H. S., Yu, A., Song, X., & Zhou, D.  
*Large Language Models Cannot Self-Correct Reasoning Yet.*  
ICLR, 2024.

**[6]** Sharma, M., Tong, M., Korbak, T., et al.  
*Towards Understanding Sycophancy in Language Models.*  
ICLR, 2024.

**[7]** Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S.  
*Reflexion: Language Agents with Verbal Reinforcement Learning.*  
NeurIPS, 2023.

**[8]** Yao, S., Yu, D., Zhao, J., et al.  
*Tree of Thoughts: Deliberate Problem Solving with Large Language Models.*  
NeurIPS, 2023.

Zhou, A., Yan, K., Shlapentokh-Rothman, M., Wang, H., & Wang, Y.-X.  
*Language Agent Tree Search Unifies Reasoning, Acting, and Planning in Language Models.*  
ICML, 2024.

**[9]** Yamada, Y., Lange, R. T., Lu, C., Hu, S., Lu, C., Foerster, J., Clune, J., & Ha, D.  
*The AI Scientist-v2: Workshop-Level Automated Scientific Discovery via Agentic Tree Search.*  
arXiv:`2504.08066`, 2025.

**[10]** Jiang, Z., Schmidt, D., Srikanth, D., Xu, D., Kaplan, I., Jacenko, D., & Wu, Y.  
*AIDE: AI-Driven Exploration in the Space of Code.*  
arXiv:`2502.13138`, 2025.
