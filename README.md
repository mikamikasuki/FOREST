# FOREST

A self-hosted workspace for long-running, agent-driven research. FOREST connects research goals, executable tasks, observed results and manuscript claims in an editable graph. Agents use your configured model provider to read sources, write code, run experiments and prepare papers; you can inspect and revise the work at any stage.

## Quick start

Install Python 3.11+ and Node.js 22+ with npm. From the project directory on macOS or Linux:

```sh
python3.11 scripts/install.py
.venv/bin/python scripts/start.py
```

Open [localhost:8000](http://127.0.0.1:8000), configure a model provider in **Settings**, then create a project with a research goal and an explicit spending budget. Build or generate a research path, run its tasks, and inspect the resulting files and evidence. **Paper** provides manuscript editing, figures, tables and single- or double-column layouts. PDF generation needs `tectonic` or `pdflatex`.

SQLite and local file storage work by default. Windows users can run through WSL2 or Docker Desktop; [deployment instructions](docs/DEPLOYMENT.md) cover containers, PostgreSQL, background services and backups. Configuration options are in [`.env.example`](.env.example).

## The problem: a research trajectory must retain its meaning

A research project outlives any one model response. An experiment answers a particular question under a particular protocol; a figure summarizes its outputs; a claim depends on both. As the project grows, losing one of these relationships can redirect later work while every individual step still looks plausible. Strong local reasoning leaves this coordination problem open.

There is a mathematical precedent. In sequential prediction, errors change the states encountered next. [Ross et al.](https://proceedings.mlr.press/v15/ross11a.html) show how this can produce a worst-case excess-cost bound quadratic in the decision horizon under their imitation-learning assumptions. Language-model studies identify related practical failures: [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/) finds sensitivity to where evidence appears in context, and [LLMs Get Lost in Multi-Turn Conversation](https://arxiv.org/abs/2505.06120) documents the persistence of early mistaken assumptions. These findings motivate explicit research state and observable feedback.

Research can also stall through excessive caution. Repeatedly listing possibilities leaves the next experiment undefined; turning a local negative result into a verdict on the whole project discards useful distinctions. FOREST's research policy asks for a best estimate, supporting and contrary evidence, a decisive unknown, and the cheapest experiment that can change the decision. Manuscripts develop the strongest supported contribution, with claims scoped to the evidence. Material contrary results remain part of that evidence.

## Research as an evolving evidence graph

FOREST stores goals, dependencies, artifacts and execution history outside the model's working context. Each task receives the current controls and relevant branch material, with access to the retained originals. Revisions make changed inputs visible; dependency checks block stale inputs at scheduling boundaries. Editing a protocol identifies the downstream work that needs recomputation or refresh. Unrelated branches remain available.

Execution records connect actions to actual process outcomes. Manuscript bindings connect reported values to saved measurements and references to source records. This follows the action-and-observation principle studied in [ReAct](https://arxiv.org/abs/2210.03629), extended here into an editable research workspace. The central benefit is inspectability: a claim has a route back to its evidence, and a changed premise has a route forward to the work it affects.

### Why checking helps—and when it does

Consider $N$ proposed handoffs, including retries. Given the preceding history, let $q_t$ be the probability that proposal $t$ violates a specified contract, and $\alpha_t$ the probability that its check accepts that violation. Conditional probability and the union bound give

$$
\Pr(\text{any invalid handoff accepted})
\leq \min\!\left(1,\sum_{t=1}^{N}\mathbb{E}[q_t\alpha_t]\right).
$$

The model influences proposal quality; executable checks influence which errors propagate. Useful checks must also admit valid progress. Under independent, identical retries with both valid and invalid candidates, checking improves accepted-result quality exactly when its false-acceptance rate is lower than its true-acceptance rate. This explains why indiscriminate rejection is a poor research strategy.

The bound concerns the properties enforced at checked handoffs. [The design note](docs/DESIGN.md) gives the assumptions, proofs, implementation correspondence and a protocol for measuring end-to-end research reliability.

## Running it well

- **Reserve experiment memory.** Prefer a capable hosted model API when experiments use the local GPU. Local inference consumes memory for weights and an expanding attention cache; long contexts and concurrent requests can exhaust memory shared with training. Use a separate inference GPU or an explicit memory budget if running locally. [PagedAttention](https://arxiv.org/abs/2309.06180) explains the cache mechanism.
- **Budget inference and experiments separately.** Provider budgets govern model calls. Worker capacity settings control task admission; container settings enforce task CPU and memory limits. Configure them before unattended work. Hosted providers receive the task context sent to them, so keep credentials and restricted data out of that context.
- **Make recovery part of the experiment.** Save model, optimizer, random and progress state in task checkpoints. Keep the database and project files on persistent storage and back them up together. [Recovery instructions](docs/DEPLOYMENT.md#backups-and-recovery) explain reconnection and checkpoint continuation.
- **Choose the execution boundary.** The default service listens on loopback, and local commands inherit the worker's operating-system permissions. Use the documented container backend for task isolation and read [SECURITY.md](SECURITY.md) before exposing the service.

## Development

```sh
./scripts/dev.sh
./scripts/test.sh
.venv/bin/python scripts/package_source.py --output output/forest-source.zip
```

Live provider tests are opt-in. Source packaging excludes local projects, credentials, logs, caches and private development reports, and checks for recognizable embedded secrets.

See [contributing](CONTRIBUTING.md), [agent runtime](docs/RUNTIME.md), [evidence workflow](docs/EVIDENCE_WORKFLOW.md), [paper authoring](docs/PAPER_AUTHORING.md) and [layout controls](docs/PAPER_LAYOUT.md). Source is licensed under [Apache-2.0](LICENSE); dependencies and imported materials retain their own licenses.
