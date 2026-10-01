# FOREST CLI

The `forest` command controls the same HTTP API, research graph, workers and evidence used by the Web workspace. Commands submit durable work to the service; closing a client terminal does not cancel that work. The CLI does not keep a separate research database.

Use the CLI for project setup, node execution, run control, logs, evidence inspection, manuscript tasks and exports. Use the Web workspace for graph exploration, Figure Studio and visual manuscript review. This release does not include a terminal graph editor, workflow YAML engine or conversational terminal agent.

## Install and start

Python 3.11+ is required. The source installer supports macOS and Linux. Windows clients can connect to an existing API, while local workers need WSL2 or Docker Desktop; native Windows process control remains unsupported.

From the repository root, install a headless API and worker without the Web build:

```sh
python3.11 scripts/install.py --headless
.venv/bin/forest --help
.venv/bin/forest serve --headless --data-dir /path/to/forest-data
```

`serve` runs the API and worker in the foreground. Keep its terminal open or use the existing [service management workflow](DEPLOYMENT.md) for a persistent deployment. Use a different terminal for client commands. Choosing `--data-dir` keeps runtime state outside the source tree; use the same directory when restarting the service.

For the Web workspace as well, use the ordinary installer and launch without `--headless`:

```sh
python3.11 scripts/install.py
.venv/bin/forest serve
```

This also requires Node.js 22+ and npm. Open `http://127.0.0.1:8000` after startup. `forest open` requires a running service with a Web build; a headless service supplies the API only.

For development in an existing environment, install the repository's locked dependencies, then the editable package:

```sh
python3.11 -m pip install -r requirements.lock.txt
python3.11 -m pip install --no-deps -e .
forest --help
python3.11 -m forest_cli --help
```

The command entry point and `python -m forest_cli` are equivalent. Run help from any directory after installation. A remote client needs Python and `httpx`; the CLI does not import the API, database or scientific runtime to display help or make requests.

For a client-only environment, install from the source checkout without the backend dependency set:

```sh
python -m pip install httpx
python -m pip install --no-deps -e .
forest --server https://forest.example.org --token-file /private/forest-owner-token project list
```

Use Python 3.11+ for this client environment as well. This setup connects to an existing service and cannot run `forest serve` without the server dependencies.

Installing or starting FOREST does not authorize paid model calls. Providers, prices and outer spending limits remain configured on the server. LaTeX is required on the execution host for PDF compilation; see [deployment requirements](DEPLOYMENT.md).

## Connection and project selection

Save the service connection:

```sh
forest connect http://127.0.0.1:8000
```

For an authenticated remote service, provide an owner-token file on the client:

```sh
forest connect https://forest.example.org --token-file /private/forest-owner-token
```

The configuration stores the file path, not the token value. Keep the file private and outside the repository. A local API accepts loopback access under its existing owner-access policy. Remote deployments need owner authentication and appropriate HTTPS configuration.

Create a project and bind it to the current directory:

```sh
forest init --name "My research" --goal-file goal.md --mode assisted
forest status
```

`init` reads the local UTF-8 goal file and sends its text to the server. It writes `.forest/project.json` in the current directory. It refuses to replace an existing binding in that directory before creating a project; use `project create` followed by `project use ID` to deliberately change the binding. That file records the project and connection binding; it contains no graph, run history or provider credentials and is ignored by Git. This binding can be used from that directory or its descendants.

For an existing project:

```sh
forest project list
forest project use PROJECT_ID
forest project show
```

`project create` accepts the same creation options as `init`, but does not change the directory binding. Use exact server-issued IDs. The optional global `--project PROJECT_ID` selects another project for one invocation.

Global configuration defaults to `~/.config/forest/cli.json`. Override its location with `--config PATH` or `FOREST_CLI_CONFIG`. Connection/project values resolve in this order: explicit flags, environment, local directory binding, global configuration, then the default service `http://127.0.0.1:8000`. A project must be selected for project-scoped commands.

| Flag | Environment | Purpose |
|---|---|---|
| `--server URL` | `FOREST_SERVER` | HTTP API base URL |
| `--project ID` | `FOREST_PROJECT_ID` | Project for this invocation |
| `--token-file PATH` | `FOREST_TOKEN_FILE` | Read an owner token from a local file |
| — | `FOREST_OWNER_TOKEN` | Supply an owner token in the process environment |
| `--config PATH` | `FOREST_CLI_CONFIG` | Global CLI configuration file |
| `--json` | — | Machine-readable output |
| `--timeout SECONDS` / `--request-timeout SECONDS` | — | Per-request HTTP timeout |

Put global flags before the command for clear scripts, for example `forest --json --project PROJECT_ID status`. `run wait --timeout` sets the total observation timeout instead of the HTTP timeout; other commands with `--wait` accept `--wait-timeout`.

## Projects and configuration files

Structured input files accept JSON or TOML objects. YAML is not supported. File contents are sent as API configuration; references to experiment paths are interpreted on the server, not on the client.

```sh
forest init --name "My research" --goal-file goal.md \
  --budget-file project-budget.json --config-file project-config.json
forest project edit --file project-edit.json
forest project export --output research-export.zip
```

Project edits accept fields such as `name`, `description`, `goal`, `current_direction`, `mode`, `budget` and `config`. The CLI submits an expected revision when editing; a concurrent Web/client edit produces a conflict instead of silently overwriting newer state. An edit file can also supply `expected_revision` when it was prepared from an earlier snapshot. Keep that revision with the edit if it must be based on that snapshot.

The default publication target remains `full_submission` and `full_paper`. CLI creation does not replace it with a pilot target. See [publication delivery](PUBLICATION_DELIVERY.md) for the existing full-submission requirements.

Paid calls remain disabled by the default project budget. Enable and bound them only through an explicit budget file when intended. Project, provider and run limits apply together; increasing a run limit does not raise an outer limit. `forest budget show` reports the selected project's usage and limits.

## Nodes and execution

```sh
forest node list
forest node show NODE_ID
forest node context NODE_ID
forest node add --file node.json
forest node edit NODE_ID --file node-edit.json --dry-run
forest node edit NODE_ID --file node-edit.json
forest node run NODE_ID --scope ancestors
```

A new node file contains graph-command parameters, for example:

```json
{
  "title": "Measure trapezoidal integration error",
  "type": "experiment",
  "instructions": "Execute the CPU calculation and retain its measured error.",
  "config": {
    "kind": "experiment",
    "command": [
      "python3",
      "-c",
      "import json; from pathlib import Path; n=5000; h=1/n; value=h*(sum((i*h)**2 for i in range(1,n))+0.5); metrics={'integral':value,'absolute_error':abs(value-1/3),'intervals':n}; Path('metrics.json').write_text(json.dumps(metrics)); print(json.dumps(metrics))"
    ]
  },
  "inputs": []
}
```

This short computation tests the execution path using an actual numerical result. Use a Python executable available on the server, and replace the command and protocol with the intended research experiment. Completing this example does not satisfy the full-submission delivery requirements.

Use `branch_id` to select a branch; otherwise the CLI uses the project's Main branch. An add file may contain `node` plus `parent_id` when adding a dependent node. An edit file contains the patch fields directly, such as `title`, `instructions`, `config` or `inputs`. Runtime state, IDs and revisions are managed by the backend.

`--dry-run` calls the graph impact preview. It does not apply the edit. The CLI uses the current graph revision when applying a command; a supplied `expected_revision` retains the caller's snapshot requirement. Previewing and applying remain separate requests, so another editor can change the graph between them.

| Run scope | Selection |
|---|---|
| `single` | The selected node |
| `ancestors` | The selected node and its upstream dependencies |
| `descendants` | The selected node and downstream work |
| `affected` | The backend's affected scope for the selected node |

Supply a run configuration with `--config-file run-config.json`. Scheduling, dependency readiness and backend selection continue to follow the server's existing rules. Submission returns run IDs; add `--wait` to observe those runs until completion or a state requiring attention.

Graph commands and queue submissions expose `--request-id` on `node add`, `node edit`, `node run`, `run retry`, `paper generate` and `paper compile`. Reuse the same ID only when retrying the same logical submission after an uncertain response. Server receipts prevent duplicate graph/queue work for that ID. The CLI does not automatically resend HTTP writes; project creation and ordinary control actions should be inspected before repeating after an uncertain transport failure.

## Observe and control runs

```sh
forest status
forest status --watch --interval 3
forest run list
forest run show RUN_ID
forest run logs RUN_ID --follow
forest run wait RUN_ID --timeout 600
forest run evidence RUN_ID
```

Status summarizes the project, controller, active runs, usage and publication audit. These are separate state dimensions: execution success does not establish scientific validity or submission readiness. Evidence output includes run lineage, node revisions and saved-file comparisons so that older results remain identifiable.

Watch and log-follow modes poll the authoritative HTTP API. They can be restarted after a client disconnect. In JSON mode, repeated watch/log output uses one JSON object per line; single responses use one JSON value.

```sh
forest run pause RUN_ID
forest run resume RUN_ID
forest run cancel RUN_ID
forest run retry RUN_ID --request-id retry-baseline-01
```

`resume` continues a paused/waiting run according to its existing runner/session behavior. `retry` queues a new run. Neither command manufactures a computation checkpoint: successful recovery still depends on retained processes or checkpoints written by the task. See [execution recovery](DEPLOYMENT.md#isolated-task-execution).

For an explicit Agent budget update during resume, use `--budget-file agent-budget.json`. The object contains limits such as `steps`, `input_tokens`, `output_tokens`, `cost` (USD) and `active_seconds`. Limits are total limits, not an increment of additional allowance. They must be positive and remain subject to the project/provider limits.

Ctrl+C exits client observation with code 130. It does not issue a cancellation request. Use `run cancel` or `research stop` to stop backend work. Ctrl+C in the foreground `serve` process stops its API/worker process group; use that only when stopping the service is intended.

## Research controller

```sh
forest research start --no-autonomous
forest research start --autonomous --max-cycles 10
forest research start --autonomous --branch BRANCH_ID
forest research pause
forest research stop
```

`--autonomous` permits the existing controller to plan more research when ready graph work is exhausted. `--no-autonomous` disables that additional planning; the controller can still execute ready nodes. Without either flag, the server retains an existing controller setting or uses the project's mode default.

`manual` and `assisted` modes do not by themselves implement a per-step approval queue in this release. If every task should be explicitly triggered, use `node run` and leave the controller stopped. Choose an explicit autonomous flag when starting the controller so that the intended behavior is visible in scripts.

`research pause` pauses the controller and eligible owned runs; `research stop` stops the controller and cancels its active work under the existing API policy. Partial process-control errors are reported. A successful controller start means the request was accepted, not that the research or manuscript is complete. Cycle/budget exhaustion can leave `submission_incomplete`.

## Manuscripts, delivery and export

Select completed evidence explicitly in `evidence.json`:

```json
{
  "run_ids": ["COMPLETED_RUN_ID"],
  "figure_ids": ["REVIEWED_FIGURE_ID"]
}
```

`run_ids` is required and must contain completed runs in the selected project. `figure_ids` is optional. The file may contain other supported manuscript-generation options; inspect current API/resource configuration when preparing them.

```sh
forest paper show
forest paper generate --evidence evidence.json --wait
forest paper check
forest paper compile --wait
forest publication check
forest paper export --format pdf --output manuscript.pdf
forest paper export --format source --output manuscript-source.zip
forest open --page paper
```

Generation submits actual model-backed work under the configured budgets. A compile task uses the server's TeX toolchain and current paper revision. `paper check` checks manuscript issues; `publication check` reports the broader delivery audit. Neither establishes venue acceptance or substitutes for scientific and visual review. See [evidence workflow](EVIDENCE_WORKFLOW.md) and [paper authoring](PAPER_AUTHORING.md).

Export writes the server's response to the requested local path and refuses to overwrite an existing file. Project ZIP exports preserve portable relative paths and omit the backend's excluded private files under its existing export rules. A project export is not a full service/database backup; use the [backup tools](DEPLOYMENT.md#backups-and-recovery) for that.

`forest open --page workspace|paper|figures|library` opens the corresponding Web page. It uses the selected project/connection and your default browser.

## Output and exit codes

Commands are noninteractive: missing files, invalid objects, missing project selection or server errors fail explicitly. Use `forest COMMAND --help` for each command's options. Human summaries go to standard output; diagnostics go to standard error. In `--json` mode, successful standard output is structured data and errors are structured diagnostics on standard error.

| Exit code | Meaning |
|---|---|
| `0` | Request/query succeeded; a waited run completed successfully |
| `1` | Transport, API or other operational error |
| `2` | Invalid input, usage or missing project |
| `3` | Authentication/authorization failure |
| `4` | Revision/state conflict or stale PDF; refresh, reconcile or recompile |
| `5` | Observed run stopped without success or needs attention; also a failed check/control result |
| `6` | Client wait timeout; backend work continues |
| `130` | Client interrupted with Ctrl+C |

`node run`, `paper generate` and `paper compile` without `--wait` return 0 when submission succeeds. With `--wait`, paused, waiting-input, budget-exhausted, failed, cancelled or interrupted states return 5; completed runs return 0. A local wait timeout returns 6 without cancelling work. HTTP timeout and total observation timeout are separate controls.

For automation, preserve the returned run ID, inspect `run show` after uncertain responses and use request IDs where supported. An incomplete publication audit or manuscript issues should be handled as work still required, not inferred to be a successful submission from an earlier command's exit code.
