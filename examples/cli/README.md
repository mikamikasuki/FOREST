# A CLI experiment without model calls

Start the service from a FOREST checkout:

```sh
python3.11 scripts/install.py --headless
.venv/bin/forest serve --headless --data-dir /path/to/example-data
```

In another terminal, use an empty working directory and the installed `forest`
command (or the checkout's absolute `.venv/bin/forest` path). Replace
`/path/to/FOREST` with the checkout path:

```sh
forest init --name "Numerical integration" --mode manual \
  --goal-file /path/to/FOREST/examples/cli/goal.md \
  --config-file /path/to/FOREST/examples/cli/project-config.json
forest node add --file /path/to/FOREST/examples/cli/node.json
forest node list
forest node run NODE_ID --wait --wait-timeout 30
forest run list
forest run evidence RUN_ID
forest budget show
forest project export --output integration-project.zip
```

Use the node and run IDs printed by the preceding commands. The experiment
executes `python3` on the worker host, calculates the integral, and saves
`metrics.json` with its observed error. It uses only Python's standard library.
It does not configure a provider, call a model, or enable paid API requests.

The explicitly selected `operational` profile scopes this example to a runtime
experiment. Scientific projects retain the default `full_submission` profile;
an operational check is not paper readiness.
