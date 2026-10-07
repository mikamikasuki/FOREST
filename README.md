# FOREST

**Give it a research question. Turn it into ideas, real experiments, evidence, and a complete paper.**

FOREST is an end-to-end research agent framework for carrying a scientific project from an early question to a finished manuscript.

It can explore and compare research directions, search the literature, turn promising ideas into testable hypotheses, design experiments, run real code on your compute, analyze the results, and decide what to try next. As the project develops, FOREST can revise its plan, abandon weak directions, branch into alternatives, and build the figures, tables, and manuscript around the evidence that survives.

Instead of keeping the research process inside a long chat history, FOREST represents it as an editable research graph. Ideas, hypotheses, experiments, results, decisions, and paper sections remain connected. You can inspect the graph, change a plan, replace a hypothesis, branch from an earlier point, or redirect the research without starting over.

**Idea discovery → literature review → hypothesis formation → experiment design → real execution → result analysis → research-path revision → baselines & ablations → figures & tables → full-paper generation**


## What FOREST does

**Finds research directions.**  
Start with a broad question or an unfinished idea. FOREST explores possible directions, compares them, checks the surrounding literature, and turns the strongest candidates into concrete research paths.

**Turns ideas into experiments.**  
Hypotheses are converted into executable plans with baselines, controls, metrics, ablations, and explicit conditions for success or failure.

**Runs real experiments.**  
FOREST writes and executes research code, launches training and evaluation jobs, collects logs and metrics, and keeps experimental artifacts attached to the research path that produced them.

**Analyzes the evidence and changes course.**  
Results are not treated as the end of a task. FOREST interprets them, compares competing explanations, identifies what failed, and decides whether to extend, revise, or drop a direction.

**Keeps the research path editable.**  
Researchers can intervene at any point: edit a node, change an experiment, revisit an earlier assumption, or branch the graph to investigate an alternative approach without losing the existing work.

**Builds scientific figures and tables.**  
Experimental results can be turned into publication-ready visualizations and tables while remaining tied to their underlying runs and observations.

**Writes the paper.**  
FOREST carries the surviving evidence into a full manuscript, including related work, methods, experiments, results, figures, tables, and discussion. Metrics, figures, and scientific claims remain traceable to the evidence used to produce them.

---

## Quick Start

Requires **Python 3.11+**. The Web workspace also requires **Node.js 22+**.

```bash
git clone https://github.com/mikamikasuki/FOREST.git
cd FOREST
```

### Web Workspace — macOS / Linux

Install dependencies and start FOREST:

```bash
python3.11 scripts/install.py
.venv/bin/forest serve
```

Open [localhost:8000](http://127.0.0.1:8000/), configure a model provider in **Settings**, and create a project with a research goal and an explicit spending budget.

### CLI / Headless

Run the API and worker without Node.js or the Web build:

```bash
python3.11 scripts/install.py --headless
.venv/bin/forest serve --headless --data-dir ./var
```

Keep the service running and use another terminal for client commands. Run these commands from the repository root:

```bash
.venv/bin/forest --help
.venv/bin/forest init --name "My research" --goal "Your research question"
.venv/bin/forest status
```

The CLI supports project and node management, experiment execution, logs, run control, evidence inspection, budgets, and manuscript tasks. It shares the same research state as the Web workspace.

See the [CLI guide](https://github.com/mikamikasuki/FOREST/blob/main/docs/CLI.md) or try the [numerical experiment example](https://github.com/mikamikasuki/FOREST/blob/main/examples/cli/README.md) without model calls.

### Repository Inputs and Diagnostics

Prepare a public GitHub repository in the selected project:

```bash
.venv/bin/forest repo clone https://github.com/OWNER/REPOSITORY.git --wait
```

Use the returned run ID to inspect its source commit and execution diagnostics:

```bash
.venv/bin/forest repo inspect RUN_ID
.venv/bin/forest run diagnostics RUN_ID
```

Git must be installed on the worker; SSH access also requires OpenSSH. Private repositories use worker-side HTTPS or SSH credential profiles.

Agent, command, and experiment tasks can prepare their own source checkout through a `repository` configuration. See [Repository Inputs and Authentication](https://github.com/mikamikasuki/FOREST/blob/main/docs/GITHUB_REPOSITORIES.md) for configuration and examples.

### Development

After installing the Web workspace:

```bash
# Start development services
./scripts/dev.sh

# Check the local environment
.venv/bin/python scripts/doctor.py

# Run backend tests, frontend compilation, and frontend tests
./scripts/test.sh
```

### Project File Uploads and Editor Revisions

Uploads through `POST /api/projects/{id}/upload` publish under the same
project-file revision protocol as editor saves. Every successful upload,
including a same-byte replacement, advances that path's revision once and
returns the compatible `{path, size, origin}` response with
`origin: "user_import"`. `GET /api/projects/{id}/file` returns bytes, revision,
and origin from one snapshot. A `PUT` using a stale `expected_revision` returns
`409 REVISION_CONFLICT` without changing the uploaded file; use the fresh GET
revision to save, or omit `expected_revision` only for an intentional
force-save.

For focused backend checks after setup, run:

```bash
.venv/bin/python -m pytest -q tests/test_file_upload.py tests/test_file_preview.py tests/test_api_contracts.py
```

The PostgreSQL concurrency and definite-abort checks use a disposable,
loopback-only test database configured through `FOREST_DATABASE_URL` and are
run separately with `tests/test_file_revision_postgres.py`. Do not point that
test at a normal application database. The shared publication guard coordinates
the supported GET, upload, and PUT endpoints, including compensation after a
definite database abort. It is not a universal filesystem transaction: process
death, power loss, ambiguous commit acknowledgements, and writes by workers or
other programs that bypass these endpoints are outside this guarantee.

### Research Modes

- **Manual** — edit the research graph and choose tasks to execute.
- **Assisted** — use model-assisted planning and tools while directing the research.
- **Auto** — enable autonomous planning and execution within configured goals, budgets, and delivery requirements.

Saved runs retain decisions, logs, metrics, artifacts, and execution history for inspection.

PDF compilation requires `tectonic` or `pdflatex`. Configure spending and execution limits before unattended runs, and keep credentials outside the repository.

See [Deployment](https://github.com/mikamikasuki/FOREST/blob/main/docs/DEPLOYMENT.md) for Docker, PostgreSQL, background services, backups, and Windows via WSL2 or Docker Desktop. See [Security](https://github.com/mikamikasuki/FOREST/blob/main/SECURITY.md) for execution boundaries.

### Runtime Notes

- Prefer a remote model API when experiments need the local GPU. Local model inference and long attention caches compete with training and evaluation for memory.
- Local execution runs generated code with the worker account’s permissions. For untrusted tasks, select the Docker execution backend and restrict mounted data, network access, and resources. A separate working directory does not provide operating-system isolation.
- Configure model spending, task duration, worker capacity, and execution limits before unattended runs. Container resource limits require the container backend.
- Recompute important results from raw artifacts.
- Record dependency versions, dataset versions, model IDs, seeds, and execution configuration for experiments supporting scientific claims.
- Keep provider credentials and machine-specific configuration outside the repository.

See [Security](https://github.com/mikamikasuki/FOREST/blob/main/SECURITY.md) and [Deployment](https://github.com/mikamikasuki/FOREST/blob/main/docs/DEPLOYMENT.md) for execution boundaries and configuration.

---

## Submission Workflow

Scientific projects default to a complete submission and `full_paper`, with real experiments, accepted-paper comparison, and a review-and-repair loop.

The workflow connects literature, experimental design, baseline reproduction, execution, independent analysis, visual selection, manuscript authoring, and submission review. Delivery checks retain missing evidence and outstanding work when a pilot finishes or a budget is exhausted.

Figures and manuscript values bind to completed runs and recorded observations. Visuals attach to argumentative paragraphs, and compilation checks their actual placement in the document.

See the [submission workflow](docs/PUBLICATION_DELIVERY.md) and [16-paper reference corpus](docs/PUBLICATION_REFERENCE_CORPUS.md). The reference corpus informs framework design; each research project selects topic-matched papers.

[Paper authoring](docs/PAPER_AUTHORING.md) explains independent visual selection, manuscript placement, and manual insertion from Figure Studio.

---

## Why FOREST

Modern language models can formulate hypotheses, write code, inspect literature, run tools, interpret results, and draft technical papers. The harder problem begins when these capabilities are composed into a research process that unfolds over hundreds of dependent decisions.

Long-horizon research creates a compounding state-management problem. As the trajectory grows, decision-relevant evidence can become harder to retrieve, unverified interpretations can propagate into later decisions, and locally reasonable actions can shift the process away from its original objective. Summarization may discard failure-relevant information, competing hypotheses can remain unresolved, and self-critique can reproduce correlated errors when it relies on the same underlying assumptions.

Existing work identifies related failure modes. *Lost in the Middle* and RULER examine how effective use of context differs from nominal context capacity [1,2]. Work on long-horizon search identifies context rot and premature termination as context grows [3](https://arxiv.org/abs/2606.29718). Goal-drift evaluations motivate explicit goal tracking across extended trajectories [4]. Research on intrinsic self-correction shows that reconsidering an answer without reliable external feedback can preserve or amplify errors [5].

FOREST frames autonomous research as a **state-control problem**: each decision should draw on an explicit goal, current dependencies, and inspectable evidence.

> **FOREST makes a research claim traceable to its evidence and a changed premise traceable to the work it affects.**

---

## Long-Horizon Research Drift

We use **Long-Horizon Research Drift** to describe the progressive divergence between the research state required by the original objective and the state that actually drives the agent.

Let:

- $g$ denote the research goal;
- $z_t$ denote the scientific state relevant to step $t$;
- $\hat{z}_t$ denote the scientific state represented to the agent;
- $a_t$ denote the next research action.

The agent selects:

```math
a_t \sim \pi_\theta(a \mid \hat{z}_t, g)
```

A transcript-centered workflow retains an accumulated history:

```math
H_t = H_{t-1} \oplus (a_t, o_{t+1})
```

As the trajectory grows, reconstructing the current goal, valid evidence, and unresolved dependencies from that history becomes an increasingly important part of every decision.

Four recurring sources of drift motivate explicit research state:

| Failure Mode | Effect |
|---|---|
| **Context dilution** | Decision-relevant evidence becomes harder to locate within accumulated context. |
| **Verification debt** | Unchecked interpretations become assumptions for later claims. |
| **Goal displacement** | Locally attractive tasks replace the original research objective. |
| **Premature conservative convergence** | Uncertainty becomes a stopping reason while useful discriminating experiments remain available. |

The final failure mode is particularly important in scientific work.

Uncertainty describes the current evidence state. A useful next decision identifies what remains unresolved and which observation could change the conclusion.

FOREST keeps uncertainty actionable: an unresolved claim can remain connected to the experiments capable of resolving it.

---

## Why Small Errors Become Large Failures

Let $\epsilon_t$ denote the conditional probability of a trajectory-breaking error at step $t$, given that no previous step has already broken the trajectory:

```math
\epsilon_t =
P(D_t = 1 \mid D_1 = 0, \ldots, D_{t-1} = 0)
```

By the chain rule, the probability that the trajectory remains valid through $T$ consequential steps is:

```math
P_{\mathrm{valid}}(T)
=
\prod_{t=1}^{T}(1-\epsilon_t)
```

If the conditional failure probabilities satisfy:

```math
\epsilon_t \ge \epsilon > 0
```

then:

```math
P_{\mathrm{valid}}(T)
\le (1-\epsilon)^T
\le e^{-\epsilon T}
```

Trajectory reliability therefore depends on both the local error rate and the number of consequential steps.

FOREST exposes intermediate state and evidence checks at the handoffs where an unsupported result could become a downstream premise.

---

## Research State as a Graph

FOREST externalizes research state into an editable graph:

```math
G_t = (V_t, E_t)
```

Nodes and associated records represent concepts such as:

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

Execution dependencies determine which tasks can run and which upstream outputs they consume. Evidence relationships connect results, sources, and claims.

Each scientific claim can retain the evidence supporting it, the experiments that tested it, its dependencies, and unresolved competing hypotheses.

The runtime tracks execution, research, and deliverable status separately. This distinction allows a task to finish executing while its scientific interpretation or manuscript contribution still requires review.

The complete graph can grow throughout a research program while each model request receives a selected working context.

Conceptually, FOREST constructs an **active frontier**:

```math
F_t = R(G_t, g)
```

Here, $R$ selects the goal, relevant branch state, dependencies, current artifacts, and immediate decision context. Retained originals remain available for further inspection.

Persistent project state and per-request reasoning context serve different purposes: the former preserves the research record; the latter supports the current decision.

---

## Goal Contract

FOREST retains the project goal, constraints, success criteria, and budgets as explicit planning inputs. These form a conceptual **Goal Contract**:

```math
C = (g, S, K, B)
```

where:

- $g$ is the research objective;
- $S$ contains success and falsification criteria;
- $K$ contains operational and scientific constraints;
- $B$ contains compute, time, model, and experiment budgets.

These inputs remain outside the rolling conversation history and participate in planning.

The planner’s trade-offs can be expressed through a conceptual action utility:

```math
U(a)
=
\lambda_g R_g(a)
+
\lambda_i I_G(a)
-
\lambda_c C(a)
-
\lambda_r R(a)
```

where:

- $R_g(a)$ represents relevance to the goal;
- $I_G(a)$ represents expected information value;
- $C(a)$ represents resource cost;
- $R(a)$ represents execution or scientific risk.

This expression describes the decision criteria. The implemented planner receives the stored project goal, constraints, budgets, graph state, and research evidence.

---

## Evidence-Grounded Research

Agent output is treated as a proposal until execution or supporting evidence establishes the relevant result.

FOREST assigns distinct responsibilities across the research workflow:

```text
Planning
   ↓
Literature / Design / Implementation
   ↓
Experiment Execution
   ↓
Independent Analysis
   ↓
Evidence and Claim Review
   ↓
Figures and Manuscript
   ↓
Submission Review
```

Code is executed. Metrics can be recomputed from original observations. Assertions are checked against referenced artifacts. Competing explanations can be examined through additional analysis or experiments. Failed operations remain visible in the research record.

A claim can therefore be rejected even when the prose describing it is persuasive.

> **LLMs propose. Evidence decides.**

---

## Independent Verification

Independent verification grounds judgments in executed code, recomputed metrics, and original artifacts.

Consider a harmful local error with conditional probability $\epsilon_t$. Let $d_t$ be the probability of detecting that error, conditional on its occurrence. Let $r_t$ be the probability of successful repair, conditional on detection.

Holding the local error opportunities fixed, and assuming verification and repair introduce no additional harmful errors, the propagated-error hazard becomes:

```math
\epsilon'_t = \epsilon_t(1-d_t r_t)
```

The corresponding survival probability under this model is:

```math
P_{\mathrm{model}}
=
\prod_t \left(1-\epsilon_t(1-d_t r_t)\right)
```

For a step with $\epsilon_t > 0$, detection and repair reduce its propagated-error probability when:

```math
d_t r_t > 0
```

This model identifies measurable evaluation targets: local error, detection and repair rates, trajectory drift, compute cost, and final-task utility. System-level reliability is evaluated through matched end-to-end comparisons.

[Design notes](docs/DESIGN.md) develop conditional bounds, progress-versus-rejection trade-offs, dependency-local repair, and their correspondence to the implementation.

---

## Branch, Challenge, Prune, Recover

Scientific search rarely follows a single monotonic path.

FOREST allows the research structure to change as evidence arrives:

```text
                 ┌── Hypothesis A ── Experiment ── Contradicted ── Prune
Goal ─ Question ─┤
                 ├── Hypothesis B ── Experiment ── Supported ── Expand
                 │
                 └── Hypothesis C ── Run Failure ── Repair ── Retest
```

Graph operations include:

```text
fork
clone
merge
split
re-parent
prune
restore
undo
redo
```

Pruning is a resource-allocation decision. When evidence resolves a direction against further investment, the branch can be removed from active work while its evidence remains available to later decisions.

FOREST also distinguishes **experiment failure** from **hypothesis falsification**. A failed run records an execution problem or missing usable evidence; a falsified hypothesis records evidence contradicting a scientific claim.

Declared dependencies identify downstream work affected by an upstream change. This supports targeted re-analysis, figure regeneration, or experiment reruns according to the nature of the edit.

---

## Premature Conservative Convergence

Preference-tuned language models can exhibit response biases, including sycophancy [6]. Intrinsic self-correction can fail without reliable external signals [5]. Long-horizon search research also identifies premature termination under extensive context [3](https://arxiv.org/abs/2606.29718).

FOREST treats stopping as an explicit planning decision.

Its research policy asks the planner to connect a stopping decision to evidence, feasibility, resource limits, or completion criteria, such as:

- the hypothesis is contradicted by evidence;
- the relevant claim is sufficiently resolved;
- a specified feasibility or budget boundary has been reached;
- available actions have negligible expected information value;
- the requested deliverable has satisfied its completion criteria.

Otherwise, the planner identifies a discriminating action for the unresolved question.

For full-submission projects, the controller also checks the current delivery audit. A completed task queue and a completed scientific submission have separate states, so remaining literature, experiment, analysis, and manuscript requirements stay visible.

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
                     │ Independent Analysis /   │            │
                     │ Evidence Review          │            │
                     └────────────┬─────────────┘            │
                                  │                          │
                                  ▼                          │
                         ┌────────────────────┐              │
                         │   Evidence Checks  │              │
                         └─────────┬──────────┘              │
                                   │                         │
                                   ▼                         │
                         ┌────────────────────┐              │
                         │   Graph Rewriter   │──────────────┘
                         └─────────┬──────────┘
                                   │
                         branch / prune / merge
                         repair / refresh
                                   │
                                   ▼
                         ┌────────────────────┐
                         │ Evidence-linked    │
                         │ LaTeX / PDF / Data │
                         └────────────────────┘
```

The API stores project state and exposes editing and inspection tools. Workers execute queued tasks and retain process outcomes. Research components handle planning, evidence analysis, figures, and manuscript generation.

---

## Research Lifecycle

A full-submission workflow follows:

```text
Goal and success criteria
→ literature and benchmark selection
→ competing hypotheses
→ executable experiments
→ evidence collection
→ independent analysis
→ branch / prune / recovery
→ confirmation studies
→ claim construction
→ evidence-linked manuscript
→ visual and submission review
→ LaTeX / PDF
```

The manuscript sits downstream of the evidence graph, allowing scientific claims to remain traceable to the experiments and artifacts that support them.

For example, an experiment saves `predictions.csv`; an analysis task consumes that file through a declared dependency and produces metrics for a figure and manuscript table. FOREST retains the run and artifact references behind those values. When a declared upstream dependency changes, the graph identifies affected downstream work for refresh.

```text
Experiment
  └── predictions.csv
         ↓ declared input
Independent analysis
  └── metrics.json
         ↓ evidence binding
Figure + manuscript table
         ↓ compilation and placement checks
Paper PDF
```

See [Evidence Workflow](docs/EVIDENCE_WORKFLOW.md) and [Manuscript Pipeline](docs/MANUSCRIPT_PIPELINE.md) for artifact bindings and authoring behavior.

---

## Interface

FOREST exposes the evolving research state directly.

The workspace includes:

- project goals and controller status;
- an editable research graph;
- hypotheses and competing branches;
- experiment execution state;
- evidence and claim relationships;
- metrics and figures;
- process logs and event streams;
- file and terminal views;
- a manuscript workspace;
- saved run history and lineage.

Research paths can be inspected and edited while preserving their evidence relationships.

Figure Studio supports editable data plots, scientific mechanism diagrams, and conceptual illustrations. The paper workspace supports source editing, figure insertion, compilation, and layout inspection.

---

## Implementation

| Layer | Stack |
|---|---|
| UI | React, TypeScript, Vite, CSS |
| Research Graph | `@xyflow/react` |
| Metrics / Figures | Python, pandas, Matplotlib; SVG/PDF output |
| Runtime | Python, FastAPI, Uvicorn |
| Experiment Execution | Supervised local subprocesses; optional Docker and SSH backends |
| Validation | Pydantic and executable protocol, evidence, and artifact checks |
| Streaming | Server-Sent Events; WebSockets for terminals |
| Persistence | SQLAlchemy with SQLite by default or PostgreSQL; project-scoped artifact files |
| Paper Export | LaTeX compilation through `tectonic` or `pdflatex` |
| Tests | pytest, Vitest, Playwright |

The local server binds to `127.0.0.1` by default.

### Repository Structure

```text
apps/web/        React and TypeScript interface
services/api/    FastAPI endpoints and persistent state
services/worker/ Task scheduling, execution, and recovery
research/        Agents, graph operations, analysis, figures, and papers
runners/         Local, Docker, and SSH execution backends
scripts/         Installation, operation, qualification, and packaging
tests/           Backend regression tests
docs/            Architecture, workflows, deployment, and security guidance
```

### Execution Boundary

The default local backend runs with the worker user’s operating-system permissions.

The optional Docker task backend applies a non-root user, a read-only root filesystem, dropped capabilities, resource limits, and disabled networking by default. Tasks receive a writable workspace mount. Configure that mount and network access according to the task’s requirements.

Running the application service in a container and selecting container execution for individual research tasks are separate configuration choices.

See [Security](SECURITY.md) for the trust model and [Deployment](docs/DEPLOYMENT.md) for backend setup.

---

## Validation and Release Qualification

The release qualification suite evaluates distinct parts of the system against explicit completion criteria.

| Dimension | Evaluated behavior |
|---|---|
| Local regression | Backend behavior, frontend compilation, and component tests |
| Agent tasks | Outputs and execution receipts for 30 bounded tasks, checked by external oracles |
| Graph scale | Graph storage, pagination, dependency preservation, and editing |
| Compute endurance | Observed continuous execution and independently recomputed prediction metrics |
| Service soak | API operations and worker execution across an observed monitoring interval |

Qualification reports record the executed configuration, results, and observed duration. The suite’s task counts and duration targets describe its evaluation scope; completion is established by the corresponding run reports.

Run local regression checks with:

```bash
./scripts/test.sh
```

See [Release Qualification](docs/RELEASE.md) for qualification commands, report formats, opt-in model tests, and the scope of each check.

---

## Relation to Existing Work

FOREST builds on several lines of research.

### Long-Context Reliability

*Lost in the Middle* [1], RULER [2], and work on context rot [3](https://arxiv.org/abs/2606.29718) examine failures in the effective use of extended context.

FOREST stores persistent research state outside the prompt and constructs a decision-specific working context.

### Goal Stability

Goal-drift evaluations [4] motivate explicit goal anchoring across long autonomous trajectories.

FOREST retains project goals, constraints, and budgets as persistent planning inputs.

### Feedback and Self-Correction

Reflexion studies feedback across agent attempts [7], while research on intrinsic self-correction examines failures when external feedback is absent [5].

FOREST supports executable checks and independent recomputation from original artifacts.

### Search Over Reasoning Trajectories

Tree of Thoughts and Language Agent Tree Search examine branching, lookahead, backtracking, and environment feedback [8].

AIDE applies structured search to machine-learning engineering [10](https://arxiv.org/abs/2502.13138). The AI Scientist-v2 extends agentic tree search to automated scientific discovery [9](https://arxiv.org/abs/2504.08066).

FOREST focuses on the **lifecycle of scientific state across the research program**.

Its graph connects dependencies between branches, shared evidence, failed experiments, revisions, recovery, and claims that reuse earlier artifacts. Search decisions and scientific provenance therefore remain accessible within the same persistent workspace.

---

## Design Principle

A long-running research agent needs persistent, inspectable project state across decisions.

The model proposes research operations. The runtime records execution. The graph carries dependencies. Artifacts carry evidence. Verification checks the grounds for advancing a claim. The project goal guides the next decision.

That separation makes a research trajectory inspectable and revisable from its objective to its final manuscript.

---

## References

**[1]** Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M., Petroni, F., & Liang, P.  
*Lost in the Middle: How Language Models Use Long Contexts.*  
Transactions of the Association for Computational Linguistics, 2024.  
[Paper](https://aclanthology.org/2024.tacl-1.9/)

**[2]** Hsieh, C.-P., Sun, S., Kriman, S., Acharya, S., Rekesh, D., Zhang, F., & Ginsburg, B.  
*RULER: What's the Real Context Size of Your Long-Context Language Models?*  
2024.  
[Paper](https://arxiv.org/abs/2404.06654)

**[3]** Xia, S., Wang, Y., Huang, Z., & Liu, P.  
*Diagnosing and Mitigating Context Rot in Long-horizon Search.*  
2026.  
[Paper](https://arxiv.org/abs/2606.29718)

**[4]** Arike, R., Donoway, E., Bartsch, H., & Hobbhahn, M.  
*Evaluating Goal Drift in Language Model Agents.*  
AAAI/ACM Conference on AI, Ethics, and Society, 2025.  
[Paper](https://doi.org/10.1609/aies.v8i1.36541)

**[5]** Huang, J., Chen, X., Mishra, S., Zheng, H. S., Yu, A., Song, X., & Zhou, D.  
*Large Language Models Cannot Self-Correct Reasoning Yet.*  
ICLR, 2024.  
[Paper](https://arxiv.org/abs/2310.01798)

**[6]** Sharma, M., Tong, M., Korbak, T., et al.  
*Towards Understanding Sycophancy in Language Models.*  
ICLR, 2024.  
[Paper](https://arxiv.org/abs/2310.13548)

**[7]** Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S.  
*Reflexion: Language Agents with Verbal Reinforcement Learning.*  
NeurIPS, 2023.  
[Paper](https://arxiv.org/abs/2303.11366)

**[8]** Yao, S., Yu, D., Zhao, J., et al.  
*Tree of Thoughts: Deliberate Problem Solving with Large Language Models.*  
NeurIPS, 2023.  
[Paper](https://arxiv.org/abs/2305.10601)

Zhou, A., Yan, K., Shlapentokh-Rothman, M., Wang, H., & Wang, Y.-X.  
*Language Agent Tree Search Unifies Reasoning, Acting, and Planning in Language Models.*  
ICML, 2024.  
[Paper](https://arxiv.org/abs/2310.04406)

**[9]** Yamada, Y., Lange, R. T., Lu, C., Hu, S., Lu, C., Foerster, J., Clune, J., & Ha, D.  
*The AI Scientist-v2: Workshop-Level Automated Scientific Discovery via Agentic Tree Search.*  
2025.  
[Paper](https://arxiv.org/abs/2504.08066)

**[10]** Jiang, Z., Schmidt, D., Srikanth, D., Xu, D., Kaplan, I., Jacenko, D., & Wu, Y.  
*AIDE: AI-Driven Exploration in the Space of Code.*  
2025.  
[Paper](https://arxiv.org/abs/2502.13138)

---

## Contributing and License

See [Contributing](CONTRIBUTING.md) for development guidance and [Security](SECURITY.md) for vulnerability reporting.

FOREST is licensed under [Apache-2.0](LICENSE). Dependencies and imported research materials retain their respective licenses.
