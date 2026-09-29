# Persistent research agents

`run_agent(run_id, workspace, config)` uses the selected actual provider. There is
no built-in total turn limit. Optional `agent_budget` fields are `steps`,
`input_tokens`, `output_tokens`, `cost`, and `active_seconds`; legacy `max_steps`
is interpreted as an explicit configured budget. Budget edits on the run take
effect at the next turn. Provider transport failures never produce replacement
responses.

The editable `agent_session.json` retains messages, reported token usage, public
action summaries, full tool observations, and the exact pending action ID.
`agent_transcript.json` is available to the model through paginated
`read_transcript`. Context compaction only changes model input; it does not erase
recorded turns. `research_memory.json` holds editable decision summaries and
unresolved questions. Session files are ordinary JSON, without content addressing
or read-only artifact restrictions.

## Real process tools

- `start_process` / `run_command`: `command` is an argv list or shell string;
  `cwd` must resolve inside the workspace. `env` and `timeout` are optional.
- `python`: writes the supplied code to the requested workspace path and starts
  the current Python interpreter. It uses the same managed lifecycle.
- `inspect_process`: observes actual PID creation time, state, and memory use.
- `read_process_output`: reads stdout or stderr by byte offset and returns the
  next offset. Logs stay on disk rather than accumulating in process memory.
- `wait_for_process`: persists a wait and raises `AgentYield`. The worker releases
  the Agent executor slot, observes the child, and resumes the same session.
- `cancel_process`: terminates the actual process group, with escalation after a
  grace period. Parent worker cancellation also visits all managed children.

Commands run with the operating user's permissions. Workspace path checks are
not an operating-system sandbox for arbitrary shell commands.

Each launch records its request before the supervisor starts. Supervisors survive
Agent interpreter exits. A transient supervisor coordination lease prevents
concurrent replay of one action ID from executing twice; it does not lock code,
results, or project editing. PIDs are checked together with process creation time.
If a supervisor vanishes and exit status cannot be observed, the process is
reported as `lost`, never as successful. Agent reasoning can inspect actual files
and explicitly resume a task's own saved state or initiate a fresh attempt.

## Completion and integration

`AgentYield` carries `status`, `wait_for`, `resume_after`, and `reason`. Waiting
sessions re-enter through the same run and workspace; they must not have their
files overwritten by a fresh branch copy. Configured budget exhaustion is a
separate state, not successful completion.

`finish` rejects active child processes, missing declared files, and omitted
`required_outputs` (also accepts planner `expected_outputs`). `metrics_file`, or
an existing default `metrics.json`, is loaded from disk into `observed_metrics` only when an actual managed process has a successful completion receipt. Model-authored numeric files alone cannot satisfy completion. Metric evidence is labelled `FILE_OBSERVATION`; scientific validity still requires source, method, data, and independent-result checks.
The result includes actual execution receipts. The model summary is explicitly
labelled as a summary, not substituted for measured numeric data.

SQL tool records use the persisted action ID. Graph commands and experiment
submissions use that same ID for replay protection. Legacy submissions lacking
an idempotency interface return an inspection-required error after an uncertain
interruption rather than being silently submitted twice.

## Validation

`tests/test_agent_processes.py` runs actual processes, including concurrent launch
replay, interpreter-exit survival, incremental logs, cancellation, configured
timeout, numerical calculation, and editable outputs.

`FOREST_LIVE_AGENT_TEST=1 .venv/bin/python -m pytest
 tests/test_live_agent_runtime.py tests/test_live_research_controller.py -q -s`
uses a real local Ollama `qwen2.5:3b` provider. These checks never substitute a mock
model. The controller check begins with an empty graph and requires two actual
prime-count implementations and a verified result. Runtime acceptance does not
establish research novelty or conference-level scientific reasoning.
