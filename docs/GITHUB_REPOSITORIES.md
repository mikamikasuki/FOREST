# Repository inputs and GitHub authentication

FOREST prepares repository source as worker-owned, durable work. Public HTTPS repositories need no authentication profile. Private repositories use an explicitly named operator profile for HTTPS or SSH. Source preparation records the resolved Git commit alongside the run's artifacts, so experiment results can be traced to the actual source version.

Git must be available on the worker. SSH transport also requires the OpenSSH client. The task Docker image includes these tools, but repository authentication happens on the worker before task execution; credentials are not copied into the task container.

## Prepare and inspect source

Select a project and queue a checkout:

```sh
forest project use PROJECT_ID
forest repo clone https://github.com/OWNER/REPOSITORY.git --ref COMMIT_OR_TAG \
  --request-id source-checkout-01 --wait --wait-timeout 600
forest repo inspect RUN_ID
forest run logs RUN_ID --follow
forest run diagnostics RUN_ID
```

Submission returns a run ID immediately unless `--wait` is used. The API validates and queues the request without cloning or calling a model. Authentication failures are worker failures, visible through the persisted run and logs. Reuse a request ID only to recover the same submission after an uncertain response; changing source options while reusing it returns a conflict.

The checkout is under `runs/RUN_ID/workspace/source` in the project's data directory by default. `--directory vendor/model` selects another relative subdirectory within that run's workspace. Absolute paths, traversal, embedded URL credentials and arbitrary Git options are rejected. Repository preparation does not execute repository code or install its dependencies.

The run's `repository.json` artifact and `repo inspect` record the repository URL, requested ref, resolved commit, workspace directory and transport. Credential contents and operator file paths are excluded. A branch or `HEAD` can move between runs; use the recorded full commit SHA as the next run's `--ref` when repeating the exact source version.

The commit identifies the initial checkout. Resuming the same run verifies that checkout's HEAD and preserves working-tree edits without fetching or resetting it. A fresh retry or new run prepares source again and may resolve a moving ref differently. Changes made during execution still need to be captured in experiment artifacts; the initial commit alone does not describe an edited source tree.

Validate a source specification locally, without network or service access:

```json
{
  "url": "https://github.com/OWNER/REPOSITORY.git",
  "ref": "COMMIT_OR_TAG",
  "directory": "source",
  "transport": "https"
}
```

```sh
forest repo validate --file repository.json
```

`url` is required. The defaults are `ref: HEAD`, `directory: source` and `transport: auto`. Auto follows the URL's transport; choosing `ssh` or `https` explicitly supports switching GitHub URLs to that transport. Both `git@github.com:OWNER/REPOSITORY.git` and `ssh://git@github.com/OWNER/REPOSITORY.git` forms are supported for SSH.

## Use source in an experiment or Agent

Add a `repository` object to an `agent`, `command` or `experiment` run configuration. The worker prepares source before executing the task, using the same validation and authentication rules as `repo clone`:

```json
{
  "kind": "experiment",
  "repository": {
    "url": "https://github.com/OWNER/REPOSITORY.git",
    "ref": "COMMIT_OR_TAG",
    "directory": "source",
    "transport": "https"
  },
  "command": ["/bin/sh", "-c", "cd source && python3 train.py"],
  "metrics_file": "source/metrics.json"
}
```

```sh
forest node run NODE_ID --config-file run-config.json --wait
forest repo inspect RUN_ID
```

Commands start at the run workspace root; the checkout does not change the executor's working directory. Use `cd source` explicitly when code expects its repository root. Installing dependencies remains an explicit task/deployment choice. Container tasks see the prepared checkout through the existing workspace mount. Remote execution uses the existing workspace transfer rules, including its symlink restrictions. Each run owns its source; a previous standalone clone is not implicitly reused by another run.

## Configure private repository access

Create an operator configuration file outside the repository and all task workspaces. Set `FOREST_GIT_CREDENTIALS_FILE` to its absolute path in the worker/service environment. The file contains named profiles; API requests carry only the name.

### HTTPS

Store a GitHub token with repository read access in a private file and reference it:

```json
{
  "profiles": {
    "github-read": {
      "type": "https",
      "token_file": "/private/forest-auth/github-token"
    }
  }
}
```

```sh
chmod 600 /private/forest-auth/profiles.json /private/forest-auth/github-token
export FOREST_GIT_CREDENTIALS_FILE=/private/forest-auth/profiles.json
forest serve --headless
```

Use another terminal to submit work:

```sh
forest repo clone https://github.com/OWNER/PRIVATE_REPOSITORY.git \
  --transport https --credential github-read --wait
```

### SSH

Provide a dedicated key authorized for the repository and a verified `known_hosts` file:

```json
{
  "profiles": {
    "github-ssh": {
      "type": "ssh",
      "identity_file": "/private/forest-auth/github-key",
      "known_hosts_file": "/private/forest-auth/known_hosts"
    }
  }
}
```

```sh
chmod 600 /private/forest-auth/profiles.json /private/forest-auth/github-key
export FOREST_GIT_CREDENTIALS_FILE=/private/forest-auth/profiles.json
forest serve --headless
```

```sh
forest repo clone git@github.com:OWNER/PRIVATE_REPOSITORY.git \
  --credential github-ssh --wait
```

SSH profiles use explicit identity and host-key verification, disable interactive prompting and ignore the host user's SSH configuration and agent. Use a key that can authenticate noninteractively in the service environment. Keep token/key files readable only by their owner; the worker validates these permissions. Known-host data can be publicly readable. Configure the profile on every worker that may claim the task.

Neither the complete `~/.ssh` directory nor `~/.gitconfig` is copied. Git configuration and hooks are isolated during source preparation; authentication helpers are temporary and cleaned up. Task containers receive source files, not authentication profiles, key files or a host SSH socket. Local tasks still run with their existing trusted worker-account permissions; use the container backend for untrusted code.

This workflow prepares reproducible inputs. It does not automatically commit, push, create pull requests, initialize submodules or fetch Git LFS content. Repository publishing remains a separate owner-controlled Git operation.
