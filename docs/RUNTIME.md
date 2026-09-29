# Research runtime

FOREST stores research state in a database and editable project files. The API accepts graph and project edits, the scheduler resolves execution dependencies, and workers run the selected agent or experiment. A model provider supplies reasoning and tool requests; process supervisors execute commands and record observed outcomes.

## Context and continuity

The retained project state includes current goals, controls, graph revisions, run records, artifacts and public action history. `ContextBuilder` selects the current node's relevant branch and input material. The agent's context store retains original controls and recorded public history for paginated retrieval. Source excerpts point to files that remain separately readable through workspace tools. Required original control pages must be read before task actions become available.

Each model request has a finite working context. Packing keeps complete native tool exchanges or replaces an exchange with a reference to its saved public record. Provider window errors trigger smaller requests and retrieval under the same spending controls. Long project histories can remain accessible across many requests; retaining a record and understanding its contents are separate operations.

There is no built-in total agent turn limit. Project budgets can bound steps, tokens, spending or active time. Budget exhaustion is recorded separately from successful completion. Session files and research memory remain editable, and the full recorded history survives context packing.

## Execution and recovery

An agent can start a managed process, inspect it, read incremental output, cancel it, or yield while it runs. Yielding releases the agent executor slot; the worker resumes the same session after the wait condition changes. Real process identities and completion receipts determine execution status. Missing exit information produces a lost or interrupted state.

Action identities protect supported tool submissions from duplicate execution after a retry. Supervisors can survive an agent interpreter exit. A restarted worker can reconnect to an existing process or container after checking its identity. If the computation itself has stopped, continuation requires the task's checkpoint and recovery configuration. The task owns the completeness of its saved scientific state.

Completion checks reject active managed processes and missing required output files. Numeric file observations require a successful managed process receipt. Inspect the commands, inputs and outputs when evaluating a result: existence and successful execution establish a narrower property than a sound experimental method.

## Editable dependencies

Graph commands require the revision that the editor read. They check project boundaries, node and edge references, and acyclic execution dependencies before publishing the revised graph. The scheduler checks current input bindings and producer runs before dependent work starts.

The impact analyzer follows declared dependencies and explicit input references. A method or data change can require recomputation; a statistical change can require renewed analysis; a wording or layout change can require rendering. This classification keeps repairs local when the declared dependencies are complete. External files and services must be represented in the task's inputs if their changes should participate in this reasoning.

## Research decisions and manuscript production

Research analysis records a best estimate, uncertainty, contrary evidence and the experiment that would change the decision. Experiment protocols identify the comparison, statistical unit, selection procedure and argumentative duty. Saved predictions support independent numerical checks. Reviewer contexts can omit earlier judgment summaries while retaining primary evidence.

Manuscript generation reads selected completed-run artifacts. Bound values resolve to saved measurements; cited records and declared critical contrary references are checked before rendering. Unbound numeric prose is flagged for review. Paper layout jobs preserve editable source and publish compiled output against its source revision. See [the evidence workflow](EVIDENCE_WORKFLOW.md) and [paper authoring](PAPER_AUTHORING.md) for the full contracts.

## Source map

| Responsibility | Implementation |
| --- | --- |
| Context selection and retained records | [`research/kernel/context.py`](../research/kernel/context.py), [`research/agents/context_store.py`](../research/agents/context_store.py) |
| Agent decisions and tool lifecycle | [`research/agents/runtime.py`](../research/agents/runtime.py), [`research/agents/policy.py`](../research/agents/policy.py) |
| Graph edits and affected work | [`research/kernel/graph.py`](../research/kernel/graph.py) |
| Scheduling and recovery | [`services/worker/scheduler.py`](../services/worker/scheduler.py), [`services/worker/main.py`](../services/worker/main.py) |
| Evidence and manuscript checks | [`research/paper/evidence.py`](../research/paper/evidence.py), [`research/validation/protocol.py`](../research/validation/protocol.py) |

See [deployment](DEPLOYMENT.md) for service management, isolation and backups, and [the design note](DESIGN.md) for the assumptions behind the reliability argument.
