# Scientific research workflow

FOREST separates task execution, evidence verification and research direction.
The editable graph connects implementation, measurement, independent checks,
analysis and manuscript preparation. Completed execution produces evidence to
inspect; acceptance applies only to the checks that actually ran.

## Evidence handoffs

Add a verification node after a producer. Its configuration declares the source
node and concrete checks. For example, a separate numerical implementation can
write its own `metrics.json` and compare selected results:

```json
{
  "kind": "verification",
  "command": ["python", "independent_evaluation.py"],
  "verification": {
    "producer_node_id": "experiment-node-id",
    "checks": [{
      "id": "recompute-primary-result",
      "kind": "numeric_compare",
      "source": "metrics.json",
      "repeat": "metrics.json",
      "pointers": ["/primary_metric"],
      "absolute_tolerance": 0,
      "relative_tolerance": 0
    }]
  }
}
```

Place the actual script in the verifier's branch workspace, or bind an editable
implementation file through its node inputs. The worker executes the command
under the selected local, Docker or SSH backend and existing resource budgets.
It checks the distinct execution receipt and current output files. A copied
result or model-authored verdict does not establish reproduction.

Consumers declare `config.required_verification: ["verification-node-id"]`.
An individual file input can also declare `verification_node_id` alongside its
`node_id`, `path` and `destination`. These bindings appear as graph dependencies
and participate in cycle checks, scheduling, selective reruns and branch copies.

Set `verification_policy: "required"` on a consumer or project to require
source-bound acceptance. Explicit required-verifier bindings also enforce the
handoff. Numerical manuscript evidence and whole JSON inputs require checks of
every numerical value they transmit. Checking one metric does not accept the
other metrics in the same file; a CSV structure check does not certify scores.

Checks support:

- `numeric_compare`: current JSON numeric results and explicit absolute/relative tolerances.
- `byte_compare`: complete nonempty files from distinct executions.
- `csv_integrity`: required columns, finite measurements and unique observation identities.
- `csv_coverage`: the declared dataset/method/condition matrix, including missing cells.
- `paired_recompute`: unit-level means, differences and counts recomputed from raw paired observations; uncertainty requires explicit sampling assumptions.

Verdicts are `unverified`, `accepted`, `rejected` or `inconclusive`. Numerical
pointers, checked paths and assumptions remain visible. Checks of file content
or arithmetic do not prove methodological validity, independence of algorithms
or scientific claims.

Changing the source, verifier, command, inputs or acceptance conditions requires
fresh verification. Files and evaluation definitions remain editable. Rejected
work stays inspectable, and repairs use the same graph editing, branching and
debugging controls.

```bash
forest node run VERIFICATION_NODE_ID --wait
forest run verification RUN_ID --json
```

The Web workspace exposes current verification in **Evidence and recovery**.
Agents can create the node with `graph_command`, execute it with
`verification_run`, and inspect service-checked results with `results`.

## Comparable experiments

Declare the scientific comparison scope in `comparison_signature`: `dataset`,
`dataset_version`, `split`, `evaluation_protocol`, `metric`, `statistical_unit`
and `budget`. The budget describes the fair comparison conditions, separately
from an agent's spending allowance or task timeout. Metric definitions can
include units and aggregation. Objective `comparison_fields` add constraints.

Missing or contradictory declarations do not create a winner. Different data,
evaluation scopes or budgets require comparable reruns. Score differences are
descriptive rankings; meaningful effects, uncertainty and confirmation remain
separate scientific judgments.

## Research direction review

**Research Direction Reviewer** examines every recorded node, including archived
routes, and the complete run and research-resource history. It receives current
goals, instructions, configurations, input/output bindings, actual source and
result text, verification findings and artifact locators. Large material previews
identify unread remainders; consequential missing material becomes explicit next
work rather than an invented finding.

The review recommends one evidence-grounded direction and its cheapest decisive
experiment. It checks goal drift, repeated failures, unsupported claim propagation,
tunnel vision and counterexample checking that no longer changes a decision.
Recommendations carry subjective probability ranges, confidence, supporting and
contrary evidence, and the required base-case research judgment.

The controller reviews repeated failures and plans without intervening execution.
Declared `research_activity: "counterexample"` tasks have an allowance of two
consecutive checks by default. Excess triggers review and replanning before more
work is dispatched. Repeated unchanged actions inside such an agent task also
yield for route repair. Periodic direction review runs after six scientific tasks.

Replanning must cite the affected records, align with the current research goal,
repair or retire the old path, and change a scientific task. Renamed retries,
title/layout edits and additional review or counterexample nodes cannot satisfy
that requirement. Previous evidence is retained.

```bash
forest research route-health --json
forest research route-review --wait
```

Review timing and allowances remain editable in the project configuration:

```json
{
  "route_review": {
    "enabled": true,
    "window": 12,
    "repeated_failures": 3,
    "counterexample_limit": 2,
    "planning_without_execution": 4,
    "review_every_runs": 6,
    "material_preview_bytes": 262144
  }
}
```

The reviewer uses the configured model and shared spending controls. Its advice
does not change the graph directly or issue an evidence acceptance receipt.
Paused work, external input and exhausted budgets stay visible for a decision.

## Scientific decisions and writing

Research judgments distinguish measured evidence, reported findings, inference,
subjective estimates and speculation. Experiments have an explicit duty:
effectiveness, mechanism, scenario value or an alternative explanation. Strong
baselines, selection history, confirmation conditions and contrary results stay
traceable.

Manuscript prose centers on the strongest supported contribution and connects
problem, gap, approach and decisive evidence. Revisions make necessary local
edits while preserving sound wording. Narrative focus cannot replace real
measurements, hide a decisive contrary result or manufacture a favorable
comparison.
