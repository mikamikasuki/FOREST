# API contracts

FOREST's Web workspace, CLI and integrations use the same `/api` service. The
contract covers project and graph editing, research resources, runs and budgets,
repository inputs, evidence verification, figures, manuscripts, files, settings
and model providers. Terminal and project-event transports have separate stream
contracts.

## Contract files

| File | Purpose |
| --- | --- |
| [API reference](API_REFERENCE.md) | Operations, request fields, response shapes, errors and state transitions |
| [Frontend integration guide](FRONTEND_API_GUIDE.md) | UI feature mapping, typed calls, refresh rules, uploads, exports and streaming |
| [OpenAPI document](../packages/contracts/openapi.json) | Machine-readable HTTP paths, schemas and media types |
| [Generated TypeScript](../packages/contracts/api.generated.ts) | `paths`, `components` and `operations` types for frontend or integration clients |
| [Integration overview](../packages/contracts/INTERFACES.md) | Shared graph, run and resource conventions |

The running service exposes the same schema at `/openapi.json`, with interactive
documentation at `/docs` and `/redoc`. The service uses its existing `/api` paths;
there is no additional version prefix to add to a request.

`services/api/contracts.py` supplies descriptions and wire-format schemas for
handlers whose Python annotations do not describe their fields. These schemas
document those handlers; they do not replace their existing validation. Flexible
configuration and scientific resource data remain JSON objects, and optional
revision guards remain optional where the handler accepts their omission.

## Use the contract from a new frontend

Build screens against the typed client in
[`apps/web/src/apiClient.ts`](../apps/web/src/apiClient.ts). It uses the existing
HTTP transport and does not introduce automatic mutation retries. Downloads,
project events and terminal sessions use their documented binary or streaming
adapters.

Treat the server's current graph, resource and manuscript revisions as the source
of truth. An optimistic edit should retain the original version until the server
accepts it. A conflict requires fetching current state and resolving the edit;
repeating a request with a fresh revision can overwrite somebody else's changes.
Likewise, enqueueing a run is different from observing its successful completion.

## Update and verify

After installing backend dependencies, run these commands from the repository
root:

```bash
.venv/bin/python scripts/export_api_contract.py
.venv/bin/python scripts/export_api_contract.py --check
.venv/bin/python -m pytest -q tests/test_api_contracts.py tests/test_contract_export.py
```

The exporter obtains the running application's OpenAPI description in a temporary
local directory. It does not launch a service, migrate a database, call a model,
or read a project's research data. Generation updates the OpenAPI document,
TypeScript types and detailed API reference together. `--check` fails when any
of those files differs, and CI runs that check before regression tests. Use the
versions in `requirements.lock.txt` when exporting so schema generation matches
the service and CI environment.

For an API change, update its schema and operation description alongside the
handler, regenerate the contract files, and check transport and revision behavior.
The compatibility tests exercise actual local API requests, including permissive
dictionary bodies, stricter graph commands, authentication, current revisions,
binary exports and event replay. They complement the broader regression suite;
schema agreement alone does not establish scientific correctness of a result.
