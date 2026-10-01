# Changelog

## Unreleased — release candidate

- Durable repository preparation through `forest repo clone`, offline specification validation and saved source-commit inspection; shared repository inputs for Agent, command and experiment tasks.
- Explicit worker-side HTTPS/SSH authentication profiles with isolated Git configuration, verified SSH host keys, temporary helpers and source manifests without credentials.
- Saved execution diagnostics through the API and CLI, preserving the existing log stream and run-control workflow.

- Installable `forest` CLI and `python -m forest_cli` client for project/node/run control, logs, evidence lineage, research controllers, manuscript tasks and exports through the existing API.
- Local connection/project binding, JSON output, revision-aware graph edits, request IDs for supported submissions, and explicit wait/interruption exit codes.
- Headless installation and foreground API/worker startup with a configurable data directory; Web building remains optional for API-only use.
- Editable research graph, file workspace, branch operations, and evidence-linked writing.
- Durable API/worker execution, managed process waiting and cancellation, and checkpoint-aware recovery.
- Real model tools with saved decisions and process/output evidence.
- Independent release qualification: 30 bounded Agent tasks, graph scale, real continuous computation, and sampled service soak.
- Source-only packaging, local test CI, and explicitly enabled paid qualification workflow.

Qualification outcomes are recorded separately in [RELEASE.md](docs/RELEASE.md) and machine-readable local reports. This list describes implemented scope, not a claim that every deployment configuration or endurance target has passed.
