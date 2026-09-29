# FOREST

### Persistent Research State for Long-Horizon Autonomous Science

**LLMs propose. Evidence decides.**

FOREST is a goal-driven research runtime for long-horizon scientific work. It gives language-model agents persistent research state, executable experiments, independent verification, and explicit control over branching, pruning, recovery, and claim formation.

A frontier model can be extremely capable at an individual step and still lose a research trajectory over hundreds of steps. FOREST is built around that distinction.

---

## Quick Start

### macOS

The repository includes:

```text
Start Forest.command
```

for local startup.

For development:

```bash
npm install
npm run doctor
npm run dev
```

Before a serious research run:

```bash
npm run verify
```

Useful commands:

```bash
npm run dev
npm run build
npm run demo
npm run doctor
npm run test
npm run test:e2e
npm run verify
npm run benchmark
npm run prepare-demo
```

Provider credentials and machine-specific configuration should remain outside the repository.

---

## Execution Modes

FOREST provides three execution modes.

### LIVE LLM

Runs the full agentic research workflow with a configured model provider and real experiment execution.

Use this mode for actual autonomous research runs.

### LOCAL BENCHMARK

Runs deterministic or controlled workloads without requiring a complete live research session.

Use this mode for:

- development;
- regression testing;
- architecture evaluation;
- reproducible comparisons;
- ablation studies.

### VERIFIED REPLAY

Reconstructs an existing run from persisted artifacts and verified events.

Use this mode for:

- demonstrations;
- debugging;
- UI development;
- result inspection;
- replay without repeating model calls or experiments.

---

## Recommended Runtime Setup

### Prefer a Remote Model API When the Local GPU Is Used for Experiments

A local LLM and a research workload compete for the same accelerator memory.

Long-context inference can also consume substantial GPU memory through the KV cache. If FOREST is simultaneously running training, vision, simulation, or other GPU-heavy experiments, placing the orchestration model on the same GPU may cause:

- out-of-memory failures;
- terminated experiment workers;
- interrupted inference;
- aborted long-running research sessions.

For a single-GPU workstation, a strong remote model API is usually the more reliable controller while the local GPU remains dedicated to experiments.

With multiple GPUs, explicitly isolate the orchestration model and experiment workers.

### Sandbox Generated Code

FOREST can execute model-generated programs and shell commands.

For autonomous runs, use an isolated workspace or container when practical. Avoid exposing:

- SSH keys;
- personal files;
- unrelated repositories;
- unrestricted home directories;
- privileged system paths.

Generated research code may install packages, spawn processes, access the network, or perform unintended operations.

### Bound Resources Before Long Runs

Set practical ceilings for:

```text
wall time
process count
API spend
GPU allocation
disk usage
experiment retries
```

A stalled process, recursive retry policy, runaway artifact stream, or checkpoint-heavy experiment can otherwise consume resources long after the research path has stopped improving.

### Keep Verification Independent

Validators should recompute results from raw inputs or persisted artifacts whenever possible.

If the validator only reads the Builder's summary, both components can inherit the same incorrect assumption. Independent execution paths make disagreement informative.

### Pin Environments for Reproducibility

Experiments intended to support scientific claims should record:

- dependency versions;
- dataset versions;
- model identifiers;
- random seeds;
- runtime configuration;
- relevant system information.

A repeatable command executed in a drifting environment is not a reproducible experiment.

### Use Replay for Interface Development

LIVE LLM mode is unnecessary when changing:

- layout;
- graph rendering;
- paper styling;
- event presentation;
- dashboard components.

VERIFIED REPLAY preserves realistic research state without repeating model calls or experiments.

---

## Why FOREST

Modern language models can formulate hypotheses, write code, inspect literature, run tools, interpret results, and draft technical papers.

The harder problem appears when these capabilities are composed into a research process that lasts hours rather than minutes.

Every action changes the state seen by the next action. Context grows. Relevant evidence becomes sparse. An early interpretation can become a premise for later reasoning. A locally reasonable repair can move the project away from its original objective. Failed experiments may be summarized imperfectly. Competing explanations can survive because nobody explicitly eliminates them. Self-critique may repeat the same assumption in different language.

Long-context research already documents parts of this failure surface.

*Lost in the Middle* and RULER show that usable context can degrade well before the nominal context window is exhausted [1,2]. Recent work on long-horizon search identifies **context rot** and increasing **premature termination** as context grows [3]. Goal-drift evaluations show that autonomous agents can gradually deviate from assigned objectives over extended trajectories [4]. Intrinsic self-correction is also unreliable: asking a model to reconsider its own reasoning without external evidence can leave errors intact or reduce performance [5].

FOREST treats autonomous research as a **state-control problem**.

Model scale reduces local reasoning error.

FOREST targets the dynamics through which small local errors become global research failures.

---

## Long-Horizon Research Drift

We use **Long-Horizon Research Drift** to describe the progressive divergence between the research state required by the original objective and the state that actually drives the agent.

Let:

- $g$ denote the research goal;
- $z_t$ denote the latent scientific state after step $t$;
- $\hat{z}_t$ denote the scientific state represented to the agent;
- $a_t$ denote the next research action.

The agent selects:

$$
a_t \sim \pi_\theta(a \mid \hat{z}_t, g)
$$

A conventional transcript-driven agent maintains a history such as:

$$
H_t = H_{t-1} \oplus (a_t, o_{t+1})
$$

where the working state is increasingly encoded in accumulated text.

This creates four recurring sources of drift:

| Failure Mode | Effect |
|---|---|
| **Context dilution** | Decision-relevant evidence occupies a decreasing fraction of active context. |
| **Verification debt** | Unchecked intermediate claims become assumptions for later claims. |
| **Goal displacement** | Locally attractive tasks gradually replace the original research objective. |
| **Premature conservative convergence** | Accumulated uncertainty becomes a reason to stop exploring even when discriminating experiments remain available. |

The final failure mode is especially important in scientific work.

Uncertainty is a property of the current evidence state. It should not automatically become a terminal action.

Statements such as:

> insufficient evidence

or:

> more research is required

have limited scientific value unless the system can identify what evidence is missing and which experiment could change the decision.

FOREST keeps uncertainty executable.

An unresolved claim remains connected to the experiments capable of resolving it.

---

## Why Small Errors Become Large Failures

Define the conditional probability of a trajectory-breaking error at step $t$ as:

$$
\epsilon_t
=
P(D_t = 1 \mid D_1 = \cdots = D_{t-1} = 0)
$$

By the chain rule, the probability that a research trajectory remains valid through $T$ consequential steps is:

$$
P(\text{valid through } T)
=
\prod_{t=1}^{T}(1-\epsilon_t)
$$

If:

$$
\epsilon_t \geq \epsilon > 0
$$

then:

$$
P(\text{valid through } T)
\leq
(1-\epsilon)^T
\approx
e^{-\epsilon T}
$$

Long-horizon reliability is therefore highly sensitive to even a small persistent local error rate.

A stronger base model lowers $\epsilon_t$.

It does not remove the multiplicative effect of trajectory length.

FOREST changes the transition process itself.

---

## Research State as a Graph

FOREST externalizes scientific state into a persistent typed graph:

$$
G_t = (V_t, E_t)
$$

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

Each scientific claim can therefore retain:

- the evidence supporting it;
- the experiments that tested it;
- the assumptions on which it depends;
- the competing hypotheses that remain unresolved.

Nodes also carry state:

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

The full graph can continue growing throughout the research program.

The model does not need to consume the entire graph at every step.

FOREST constructs an **active frontier**:

$$
F_t = \operatorname{Retrieve}_K(G_t, g)
$$

where $F_t$ contains the goal, relevant hypotheses, unresolved evidence, dependencies, current artifacts, and immediate decision context.

Persistent research state may grow for hours.

The active reasoning state does not need to grow at the same rate.

---

## Goal Contract

Every FOREST run begins with a **Goal Contract**.

A Goal Contract can be represented as:

$$
C = (g, S, K, B)
$$

where:

- $g$ is the research objective;
- $S$ contains success and falsification criteria;
- $K$ contains operational and scientific constraints;
- $B$ contains compute, time, model, and experiment budgets.

The Goal Contract remains outside the rolling conversation history.

Candidate actions are continuously evaluated against it.

This turns goal adherence into an explicit runtime property rather than a sentence near the beginning of a long prompt.

A planner can be interpreted through an objective such as:

$$
U(a)
=
\lambda_g R_g(a)
+
\lambda_i IG(a)
-
\lambda_c C(a)
-
\lambda_r R(a)
$$

where:

- $R_g(a)$ measures relevance to the Goal Contract;
- $IG(a)$ measures expected information gain;
- $C(a)$ represents resource cost;
- $R(a)$ represents execution or scientific risk.

The exact scoring policy may vary across workflows.

The invariant is that the next action is selected from the current scientific state rather than from narrative momentum alone.

---

## Zero-Trust Research

Agent output is treated as a proposal until an external consequence supports it.

FOREST separates several roles:

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

Code is executed.

Metrics are recomputed.

Assertions are tested against artifacts.

Competing explanations can be challenged.

Failed operations remain visible in the research state.

A downstream claim can be rejected even when the prose describing it is persuasive.

The governing principle is simple:

> **LLMs propose. Evidence decides.**

---

## Independent Verification

Self-reflection is useful for proposing possible mistakes.

Verification should come from a different information path whenever practical.

FOREST supports independent checks over executable artifacts and persisted results instead of relying only on the narrative produced by the agent that created them.

Suppose a harmful local error occurs with conditional probability:

$$
\epsilon_t
$$

Let:

$$
d_t
$$

be the probability that independent verification detects the error, and let:

$$
r_t
$$

be the probability that the recovery path successfully repairs the detected error.

Under the simplifying assumption that successful repair prevents the local failure from propagating, the effective hazard becomes:

$$
\epsilon'_t
=
\epsilon_t(1-d_t r_t)
$$

The corresponding trajectory survival probability becomes:

$$
P_{\text{FOREST}}
=
\prod_t
\left[
1-\epsilon_t(1-d_t r_t)
\right]
$$

Whenever:

$$
d_t r_t > 0
$$

independent verification reduces the effective probability of propagated error.

This is a mechanism-level argument, not an empirical performance claim.

Its assumptions are explicit and testable. Benchmark experiments can estimate:

- local error rate;
- detection rate;
- recovery rate;
- trajectory drift;
- compute cost;
- final-task utility.

---

## Branch, Challenge, Prune, Recover

Scientific search rarely follows a single monotonic path.

FOREST allows the research structure to change as evidence arrives.

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

Pruning is a resource-allocation operation.

Once a research direction is contradicted by evidence, it should stop consuming model tokens, GPU time, and experimental budget.

Its evidence remains available in the graph so that the same failed direction does not need to be rediscovered later.

FOREST also distinguishes:

```text
experiment failure
```

from:

```text
hypothesis falsification
```

These are different scientific events and should remain separate in the research state.

---

## Premature Conservative Convergence

Preference-tuned language models can exhibit systematic response biases, including sycophancy [6]. Intrinsic self-correction can fail without reliable external signals [5]. Long-horizon search research further suggests that models can terminate early or produce uncertain incorrect answers as context grows [3].

FOREST treats this as a control-flow problem.

An agent may express uncertainty at any stage.

Terminating a branch requires a concrete condition:

- the hypothesis is contradicted by evidence;
- the relevant claim is sufficiently resolved;
- a predefined feasibility or budget boundary has been reached;
- available actions have negligible expected information gain;
- the Goal Contract has been satisfied.

Otherwise, uncertainty remains an unresolved node and the planner searches for a discriminating action.

This preserves scientific calibration while preventing generic caution from becoming an implicit stopping policy.

A branch with weak evidence remains weak.

The runtime simply requires that weakness to remain operationally explicit.

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
→ executable micro-experiments
→ evidence collection
→ challenge and verification
→ branch / prune / recovery
→ holdout confirmation
→ claim construction
→ evidence-linked manuscript
→ HTML / PDF
```

The manuscript is downstream of the evidence graph.

Scientific conclusions can therefore be traced back to the experiments and artifacts that support them.

---

## Interface

FOREST exposes the evolving research state directly instead of hiding it behind a chat transcript.

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

The current local implementation uses:

| Layer | Stack |
|---|---|
| UI | React, TypeScript, Vite, Tailwind |
| Research Graph | `@xyflow/react` |
| Metrics / Figures | Recharts, SVG |
| Icons | Lucide |
| Runtime | Node.js, TypeScript, Express |
| Experiment Execution | `worker_threads` and real local processes |
| Validation | Zod |
| Streaming | Server-Sent Events |
| Persistence | Filesystem-backed `runs/<runId>/` artifacts |
| Paper Export | Playwright |
| Tests | Vitest, Playwright |

The local server binds to:

```text
127.0.0.1
```

by default.

---

## Relation to Existing Work

FOREST builds on several lines of research.

### Long-Context Reliability

*Lost in the Middle* [1], RULER [2], and recent work on context rot [3] show that nominal context length overstates reliable long-context reasoning.

FOREST therefore stores persistent research state outside the prompt and constructs a decision-specific active frontier.

### Goal Stability

Goal-drift evaluations [4] motivate explicit goal anchoring across long autonomous trajectories.

FOREST stores the Goal Contract as persistent runtime state and uses it throughout planning.

### Feedback and Self-Correction

Reflexion demonstrates the value of feedback across agent attempts [7].

Later work shows that intrinsic self-correction without external signals can be unreliable [5].

FOREST therefore prioritizes executable and independently recomputed feedback.

### Search Over Reasoning Trajectories

Tree of Thoughts and Language Agent Tree Search demonstrate the value of branching, lookahead, backtracking, and environment feedback [8].

AIDE applies structured search to iterative machine-learning engineering.

AI Scientist-v2 extends agentic tree search toward automated scientific discovery [9,10].

FOREST focuses on the **lifecycle of scientific state across the entire research program**.

Its graph can express:

- dependencies between branches;
- shared evidence;
- contradictions;
- failed experiments;
- supersession;
- recovery;
- claims that reuse evidence produced elsewhere in the graph.

Search policy and scientific provenance therefore share the same persistent state.

---

## Design Principle

A long-running research agent should never need to remember the project solely because its transcript happens to contain the project.

The model is a stochastic research operator.

The runtime carries scientific state.

The graph carries dependencies.

Artifacts carry evidence.

Verification controls promotion from observation to claim.

The Goal Contract controls direction.

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
