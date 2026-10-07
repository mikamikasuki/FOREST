# FOREST Agent Instructions

These instructions apply to all Codex work and pull-request reviews in this
repository.

## Pull Request Review Policy

When reviewing a pull request, act as an independent merge reviewer.

The goal is to determine whether the exact reviewed commit is safe to merge,
not to maximize the number of findings.

Do not trust the PR description, author summary, or passing tests by themselves.
Inspect the actual diff and the relevant surrounding code.

### Review priorities

Pay particular attention to:

- root-cause correctness;
- current vs historical run state;
- execution provenance and source revisions;
- scientific evidence integrity;
- stale artifact consumption;
- persistence and transaction correctness;
- PostgreSQL concurrency and lock ordering;
- race conditions and lost updates;
- retry / pause / resume / cancel semantics;
- worker and subprocess lifecycle;
- timeout and budget accounting;
- database migration idempotency;
- SSE/event cursor correctness;
- reload/restart/recovery behavior;
- backward compatibility;
- whether tests exercise the actual failure path.

Trace outside changed files when necessary to verify the behavior.

## Finding quality

Report a finding only when there is a concrete, well-supported failure path.

Do not invent a finding merely to avoid approving a PR.

Prefer no finding over a speculative finding.

Do not block merge for:

- style or naming preferences;
- optional refactoring;
- additional hardening without a concrete failure path;
- unrelated pre-existing defects;
- hypothetical deployment assumptions that do not apply to FOREST;
- additional tests that would be nice to have but are not needed to establish
  correctness.

### Severity

P0 and P1 findings block merge.

P2 findings block merge when they identify a concrete correctness,
concurrency, persistence, provenance, lifecycle, migration, or user-visible
failure introduced or exposed by the PR.

P3 findings normally do not block merge.

For every material finding state:

- severity;
- exact file/function/code path;
- concrete failure scenario;
- whether the PR introduces or exposes it;
- whether it blocks merge;
- confidence: HIGH / MEDIUM / LOW.

For concurrency findings, provide a concrete interleaving or lock-order path
rather than saying only that a race "may" exist.

## Updated PRs

When reviewing a new commit after previous feedback, explicitly re-check prior
findings.

Mark each previous blocking finding as one of:

- RESOLVED
- STILL PRESENT
- PARTIALLY RESOLVED
- CANNOT VERIFY

A finding that has been fixed must not continue blocking the PR merely because
it existed in an earlier revision.

## Merge decision

Every completed PR review should give a clear merge recommendation.

Use:

MERGE VERDICT: APPROVE

when no unresolved P0, P1, or merge-blocking P2 finding remains and the
available validation is sufficient.

Use:

MERGE VERDICT: REQUEST CHANGES

when at least one concrete merge-blocking defect remains.

Use:

MERGE VERDICT: NEEDS VALIDATION

only when there is no confirmed blocker but a necessary runtime or validation
result is genuinely unavailable.

Do not use NEEDS VALIDATION merely to avoid making a decision.

End the review with:

MERGE VERDICT: APPROVE | REQUEST CHANGES | NEEDS VALIDATION
MERGE NOW: YES | NO

If no concrete merge-blocking issue is found after reasonable review, approve
the PR.
