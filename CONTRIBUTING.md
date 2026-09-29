# Contributing

Start with a small, reproducible problem or proposed behavior. Explain the user-visible outcome and attach the smallest safe input that reproduces the issue. Keep provider credentials, owner tokens, private datasets, and full runtime databases out of issues and pull requests.

## Local setup

Follow the [README](README.md), then run `./scripts/test.sh`. The default tests use temporary SQLite databases and actual local processes. Frontend checks include TypeScript compilation and component tests. PostgreSQL, live models, remote hosts, and paid integrations are opt-in; explain which were actually exercised.

Use a separate local data directory when changing persistence or execution behavior. Do not run development migrations or test cleanup against a user's working project.

## Changes

- Keep working research data editable. Use plain revisions and explicit run references.
- Preserve failed runs and contrary measurements. Do not turn missing evidence into a successful result.
- Validate output artifacts independently of the model's self-report.
- Use the existing graph commands and execution runtime so UI and Agent behavior agree.
- Keep model calls sequential unless a test explicitly measures concurrency. Configure the shared spend guard before any paid integration run.
- Include focused regression checks for execution, persistence, or security changes. For small copy or style edits, existing checks and visual inspection can be sufficient.

A pull request should state the problem, resulting behavior, relevant tests and their actual outcomes, and remaining limits. Record any change to evaluation inputs, task prompts, scoring, or resource budgets; comparisons require rerunning both conditions with the same evaluation definition. Do not select only favorable retries when reporting qualification results.

## Release evidence

Use `scripts/qualify_release.py` and retain its reports. Re-running a failed task creates another attempt; it does not erase the failure. Four-hour compute and 24-hour soak labels require the full observed durations. Graph editing at 1,000 nodes is a separate capability from running 1,000 computations.

Source archives should be produced with `scripts/package_source.py`. Review the resulting archive before publication. Contributions are distributed under the repository's Apache-2.0 license; preserve attribution and license notices for any third-party material you add.
