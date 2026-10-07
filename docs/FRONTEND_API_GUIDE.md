# Frontend API integration

FOREST's HTTP API is the boundary between the research state and the Web UI. A
replacement UI can use the same projects, editable graphs, files, runs, research
records and manuscript workflows. CLI commands use the same server.

## Contract and clients

The machine-readable contract is in
[`packages/contracts/openapi.json`](../packages/contracts/openapi.json), with
TypeScript definitions in
[`packages/contracts/api.generated.ts`](../packages/contracts/api.generated.ts).
See the [API reference](API_REFERENCE.md) for endpoint descriptions and request
fields. Generated types describe the wire format; they do not execute runtime
validation or constrain an extensible object's scientific contents.

The Web application has two JSON adapters:

- [`api.ts`](../apps/web/src/api.ts): `api<T>(path, method = "GET", body?)` is the
  existing transport. Relative paths such as `/projects` get an `/api` prefix;
  full `/api/...` paths are also accepted. Its display helpers and graph dependency
  helpers remain available to existing screens.
- [`apiClient.ts`](../apps/web/src/apiClient.ts): `requestApi()` checks documented
  route templates, HTTP methods, parameters, request bodies and JSON response
  types. It delegates requests to `api()` and adds no retries or background work.
  Use this adapter for new screens. Downloads, SSE and terminal WebSockets use
  their separate transports below.

```ts
import { requestApi } from "./apiClient";

const projects = await requestApi("/api/projects", "get", {
  query: { archived: false, limit: 100, offset: 0 },
});
const graph = await requestApi("/api/projects/{ident}/graph", "get", {
  path: { ident: projectId },
});

const result = await requestApi("/api/projects/{ident}/graph/commands", "post", {
  path: { ident: projectId },
  body: {
    request_id: crypto.randomUUID(),
    expected_revision: graph.revision,
    operation: "edit_node",
    targets: [nodeId],
    params: { instructions: revisedInstructions },
    run: false,
  },
});
// Replace the graph cache with result.graph rather than synthesizing a revision.
```

`requestApi()` uses lowercase methods. Path values are URL-encoded. Query keys
and values use `URLSearchParams`; `false`, `0` and empty strings are retained,
while `undefined` and `null` are omitted. Arrays serialize as repeated keys.
Body objects serialize to JSON; `FormData` passes through without a manually
specified content type. An omitted body and an explicit `null` are different.

JSON responses are returned directly: collection endpoints return arrays;
operation endpoints return their own objects. There is no universal `data`
envelope. HTTP 204 returns `undefined`. IDs and relative paths are strings;
timestamps are ISO strings. Run timestamps, process IDs, exit codes, errors,
node IDs and branch IDs can be `null`.

## Feature map

Paths in this table are relative to `/api`, except the terminal WebSocket.
`{ident}` means the identity described in the corresponding row, not always a
project ID. Use the generated contract for complete parameters and defaults.

| UI capability | API operations | UI state to retain |
|---|---|---|
| Project list and editor | `GET/POST /projects`; `GET/PATCH/DELETE /projects/{ident}`; `POST /projects/{ident}/duplicate` | Project ID, revision, mode, goal, budget and config. Lists support `limit`, `offset`, optional `archived`. |
| Project backups | `POST /projects/{ident}/export`; `POST /projects/import` | Export selections and imported project ID. Export is a ZIP; import uses multipart `file`. |
| Editable research graph | `GET /projects/{ident}/graph`; `GET /projects/{ident}/graph/page`; `POST /projects/{ident}/graph/preview`, `/graph/commands`, `/graph/batch` | Graph revision, branch selection, node/edge IDs, impact preview, command receipt. |
| Node editor and context | `GET/PATCH /nodes/{ident}`; `GET /nodes/{ident}/context`; `POST /nodes/{ident}/context/rebuild` | Node fields, explicit inputs, context overrides; graph revision for node edits. |
| Branch comparison | `GET /branches/compare?left=...&right=...` | Two branch IDs from the same project and returned comparison/runs. |
| Execution submission | `POST /nodes/{ident}/run`; `POST /branches/{ident}/run`; `POST /projects/{ident}/runs/selected` | Request ID, scope/config, returned run IDs. A node submission can return one run or a run collection. |
| Run inspection and controls | `GET /projects/{ident}/runs`; `GET /runs/{ident}`; `POST /runs/{ident}/{action}` | Current run status, tool executions, metrics, output paths; actions `pause`, `resume`, `cancel`, `retry`, `skip`, `priority`. |
| Run output and diagnostics | `GET /runs/{ident}/output`; `/diagnostics`; `/repository` | Output byte cursor, saved diagnostics and source provenance. |
| Agent session, provenance and verification | `GET /runs/{ident}/session`, `/lineage`, `/verification`; `POST /verification/run` | Session/transcript summary, bound input runs, verification receipt and status. Missing session is `null`. |
| Run configuration and debug checkpoint | `PATCH /runs/{ident}/configuration`, `/checkpoint` | Editable config and checkpoint payload; preserve runner-owned identity fields. |
| Research planning and control | `GET /projects/{ident}/research`, `/research/route-health`; `POST /projects/{ident}/research/{action}`, `/research/route-review`; `PATCH /projects/{ident}/objective` | Controller phase/status, comparisons, route signals, objective; actions `start`, `pause`, `stop`. |
| Research proposals | `POST /research/ideas`, `/research/suggest-paths`; `POST /ideas/{ident}/adopt`; `POST /research/proposals/{ident}/apply` | Generated proposal record, selected command indices and latest graph revision. |
| Publication requirements and delivery | `GET /publication-profile`, `/writing-policy`; `GET/PATCH /projects/{ident}/publication`; `POST /projects/{ident}/protocol/validate` | Editable profile, declared experiment design, audit gaps, validation scope. |
| Literature | `POST /library/search`, `/library/import`, `/library/compare`; `GET /library/{ident}/passages`; `POST /library/{ident}/reindex`; `GET /library/export-bibtex`; `POST /browser/read` | Source record IDs, full-text passages and export format. Search/import/read have different responses. |
| Research records | `GET/POST /{resource}`; `GET/PATCH/DELETE /{resource}/{ident}` | `project_id`, `title`, `status`, `revision`, extensible `data`. Resource names listed below. |
| Theory tools | `POST /theory/check` | Expression, variable and operation; returned symbolic/numerical result and record. |
| Experiment launch and comparison | `POST /experiments/{ident}/launch`, `/experiments/compare` | Experiment record, explicit run IDs, measured metrics and comparability verdict. |
| Repository inputs | `POST /projects/{ident}/repositories/clone`; `GET /runs/{ident}/repository` | Repository URL/ref/directory/transport/profile name, queued run, recorded source commit. |
| Data and statistical analysis | `POST /analysis/run`; `GET /data/{ident}/rows`; `POST /projects/{ident}/statistics/paired`, `/statistics/review` | Source paths/run IDs, field mappings, units, comparison scope, queued analysis/review IDs. |
| Figure specification, revision and rendering | Record endpoints for `figures`; `POST /figures/{ident}/render`, `/revise`; `GET /figures/{ident}/export` | Figure revision, completed source runs, spec/style/code, selected region and real output paths. |
| Manuscript editor and generation | `GET/PATCH /papers/{ident}`; `POST /papers/{ident}/generate`, `/figures`, `/revise` | Paper ID, project ID, paper revision, source/BibTeX, figure bindings, queued generation/revision. |
| Manuscript review and layout | `POST /papers/{ident}/check`, `/layout`, `/compile`; `POST /reviews`; `POST /reviews/{ident}/apply`; `POST /projects/{ident}/writing/review` | Current paper revision, proposed exact edits, selected indices, layout/compile runs, compiled revision. |
| Manuscript exports | `POST /papers/{ident}/export` | PDF versus source ZIP and expected paper revision. |
| Files, table preview and upload | `GET /projects/{ident}/files`; `GET/PUT/DELETE /projects/{ident}/file`; `GET /projects/{ident}/file/preview`; `POST /projects/{ident}/file/rename`, `/upload`; `GET /projects/{ident}/download` | Project-relative paths, file revision, upload destination; preview offset/limit and clipping metadata. |
| Models, hosts and agents | `GET/POST /providers`, `/hosts`, `/agents`; `PATCH /providers/{ident}`, `/hosts/{ident}`, `/agents/{ident}`; provider `/test`, `/models`, `/usage`; host `/test` | Connection configuration, provider/host/agent IDs, returned connection status. Provider responses contain `has_key`, not API keys. |
| Settings, usage and system status | `GET/PATCH /settings`; `POST /settings/cleanup`; `GET /system`, `/health`, `/projects/{ident}/usage` | Defaults, real resource availability, estimated/reserved costs and unknown limits. |
| Owner login and read-only sharing | `GET /auth/status`; `POST /auth/login`; `POST /projects/{ident}/share`; `GET/DELETE /shares/{token}` | Authentication state, selected share scope, share URL/token and revocation result. |
| Project events and live terminal | `GET /projects/{ident}/events`; WebSocket `/ws/projects/{ident}/terminal` | Event connection and sequence cursor (`Last-Event-ID`); terminal connection lifecycle, raw input/output. |

Generic record resources are `library`, `experiments`, `datasets`, `ideas`,
`theories`, `claims`, `figures`, `analyses` and `reviews`. Their lists require
`project_id`; pagination is `limit`/`offset`. Reviews are created through the
specialized `POST /reviews` operation. PATCH merges the first level of `data`;
provide a complete replacement value for each nested key being changed.

`GET /papers/{ident}` accepts a project ID to obtain its manuscript and creates
the initial draft if that project has no paper yet. Most paper
editing/compile operations also resolve a paper ID or create the project draft
where appropriate. Retain both returned `id` and `project_id`, rather than
assuming they are interchangeable across every endpoint.

## Revisions, paths and dependent outputs

Keep fetched server state and unsaved editor state separately. Retain the
revision that was loaded into the editor even when background refreshes bring
newer data. Submit that revision as `expected_revision`; replacing it with a
newer value without merging edits defeats conflict detection.

| Edit | Revision used | Result to refresh |
|---|---|---|
| Project fields | `project.revision` | Project and graph/context views affected by the change. |
| Graph command, graph batch, node PATCH | Current **graph/project** revision | Graph, impact and affected run/output views. `node.revision` is not the guard for a node PATCH. |
| Generic record, figure specification/revision | Record/figure revision | Record and consumers marked stale. |
| Manuscript source, layout, compilation, figure insertion, review application | Paper revision | Paper, compile/layout runs and preview; review proposals also bind their original paper revision. |
| Text file write | Revision returned by file read | File and dependent research outputs. An untracked file initially reads as revision 0. |

Graph commands support adding/editing/deleting nodes and dependencies, inserting
nodes, forking/cloning/reparenting paths, splitting/grouping nodes, merging
branches, pruning/restoring/selecting branches, and undo/redo. Preview an edit
before applying it when the user needs to inspect its impact. The command
response supplies the authoritative graph and impact; the UI must not compute
its own graph revision or silently rerun affected work.

`depends_on` and `consumes` edges define execution order. Explicit input and
verification bindings can also create execution dependencies. Existing
`executionLinks()` and `hasCycle()` helpers include these implicit dependencies.
Reference/history/evidence relations do not by themselves impose execution
order. Preserve `inputs`, `outputs`, `context_overrides`, comments, configuration
and edge relations when replacing the graph canvas.

On HTTP 409, preserve the draft, reload the authoritative state, show the changed
fields or impact, then merge or ask the user to reapply. Undoing graph edits does
not undo effects already executed on a machine. The file editor, graph editor
and manuscript editor share state through the server; a UI file change can
invalidate a manuscript or dependent result.

Paths are relative to the project workspace. Send file paths as encoded query
parameters rather than concatenating unescaped strings. Use the file endpoints
for source/specification edits and the graph/run endpoints for scheduling.
Repository credentials are named operator profiles configured on the worker;
repository submissions do not accept inline keys or host credential paths.

## Request identity and asynchronous work

Creating a run is an enqueue operation. A successful HTTP response with a run in
`queued` status does not mean an experiment, model request, figure or PDF has
finished. Read the returned run identity and observe its current status before
displaying its artifacts as complete.

Scheduler states include `queued`, `running`, `pausing`, `paused`, `waiting`,
`waiting_input` and `budget_exhausted`. Terminal states are `completed`, `failed`,
`cancelled`, `interrupted` and `skipped`. Budget exhaustion and editable debug
checkpoints can be resumable; do not treat every non-running state as success.
Task completion, verification success and submission readiness are distinct
results and have their own endpoints.

Usage amounts are estimates from configured model rates. Display reserved
spending separately from estimated cost and preserve `null` for unknown limits.
Provider summaries marked `scope: "all_projects"` describe the provider's shared
allowance; do not present that amount as an independent allowance for each
project. The project budget and the provider budget are separate controls.

The project `budget.seconds` value is a cumulative wall-elapsed allowance across
project runs. The scheduler reserves time at admission, checks it again before
dispatch and Resume, and includes elapsed time since the last worker checkpoint
for running, live-paused, or yielded-wait runs. Local task deadlines are checked
every 0.4 seconds;
the worker allows 0.1 seconds for graceful process shutdown before force-stopping
it. The measured stop time is charged to the project, so a task can pass its
individual deadline by roughly one check interval plus shutdown grace; host
scheduling and external-runner cancellation can add delay.

Generate one `request_id` for one intended operation and retain it until the
acceptance is known. Graph commands/batches and supported enqueue operations
store receipts or reuse run identities. This is not a global idempotency promise
for every POST/PATCH/DELETE: read the endpoint's contract. Repository cloning
also compares the stored source specification and rejects a mismatched ID.

Neither Web adapter retries automatically. After a network error during a
mutation, first inspect the graph/run state and original operation identity.
Retry only where that endpoint supports recovering the same request. A new
request ID means a new operation. The CLI likewise submits once and surfaces
the original request ID when an accepted operation cannot be confirmed.

For run output, send `offset=0` initially, append returned `text`, then use the
returned `offset` for the next request. It is a **byte offset**, including bytes
removed by a `search` filter; never calculate the next cursor from JavaScript
string length. A retry of the same output read should not append the same chunk
twice. Stop normal polling for terminal runs after the final chunk is read.

For tables, `GET /data/{ident}/rows` resolves a run or analysis record
identity, with optional `path`, `offset`, `limit`, `filter`, `sort` and `descending`.
`GET /projects/{ident}/file/preview` is a bounded source-file preview for CSV,
TSV and Parquet. These pagination offsets count rows, not output bytes. Render
returned clipping/truncation metadata rather than implying the excerpt is the
complete dataset.

## Authentication and errors

The Dashboard uses same-origin requests and the HttpOnly `forest_owner` cookie.
Valid local loopback access establishes this cookie automatically. For remote
access, `POST /api/auth/login` sends `{ "token": "..." }` and establishes the
same cookie; `GET /api/auth/status` reports authentication. The CLI can use
`Authorization: Bearer ...`. Do not place owner tokens in URLs or browser
storage. `/api/health`, auth endpoints and share access have their own public
rules; deleting a share still requires owner authentication.

For a separate development frontend, keep the Vite `/api` and `/ws` proxy or
provide equivalent same-origin routing. The server enforces origins; the Web
client is not configured for arbitrary cross-origin authenticated requests.

Ordinary failures use:

```json
{
  "detail": {
    "code": "REVISION_CONFLICT",
    "message": "Manuscript changed in another editor",
    "retryable": false,
    "suggestion": "Reload and merge changes."
  }
}
```

FastAPI request-validation failures instead have a `detail` array containing
field locations and messages. Plain-string details and non-JSON server failures
are also handled by the existing transport. `ApiError` exposes `status`, `code`,
`message` and `suggestion`; it does not preserve every raw detail field. Existing
codes have differing case, so use the documented code or HTTP status rather
than assuming all codes are uppercase. Network failures remain browser fetch
errors; successful responses with invalid JSON remain parsing errors.

`api()` dispatches `forest-auth-required` on HTTP 401. The application uses it
to open the owner login dialog. `download()` retains its separate error
behavior: it throws `ApiError` on failure and does not dispatch this event.
Handle download authentication failures explicitly in a new UI. A provider
connection test performs a real model request and can consume the configured
allowance; do not call it repeatedly as a background health check.

## Events, downloads and terminal

Project events use native `EventSource`:

```ts
const events = new EventSource(`/api/projects/${encodeURIComponent(projectId)}/events`);
const refresh = () => invalidateProjectCaches(projectId);
events.addEventListener("node_changed", refresh);
events.addEventListener("run_changed", refresh);
events.addEventListener("paper_changed", refresh);
events.onopen = refresh;
// Unmount/project switch: events.close().
```

The server emits `connected`, named event records with monotonic `id` values,
and heartbeat comments. Browser reconnections send `Last-Event-ID`. Events are
invalidation hints: refetch authoritative resources instead of rebuilding state
from partial event data. The existing UI listens for `node_changed`,
`run_started`, `run_progress`, `run_finished`, `tool_finished`,
`metric_available`, `artifact_available`, `compile_finished`, `graph_changed`,
`project_changed`, `paper_changed`, `controller_changed`, `run_changed` and
`changed`, and refreshes on open. Include all event families needed by a new
screen and coalesce bursts of refetches. Reconnect/open must refresh current
state even if no event has arrived. Close connections on project switch.

Downloads return bytes, not JSON. Use the existing `download(path, body?, name?)`
adapter or a dedicated binary client:

```ts
import { download } from "./api";

await download(`/papers/${paperId}/export`, {
  format: "pdf", expected_revision: paper.revision,
});
await download(`/projects/${projectId}/download?${new URLSearchParams({ path: assetPath })}`);
```

The presence of a body selects POST; no body selects GET. An explicit filename
takes precedence over `Content-Disposition`, followed by `forest-export.zip`.
The adapter obtains a blob, clicks a temporary anchor and revokes its object URL
after one second. PDF export requires a PDF compiled for the current paper
revision; `STALE_PDF` requires recompilation. Figure exports require an available
rendered format. Do not substitute a path string for an artifact completion
check.

Upload and backup import use multipart `file`. The optional upload `directory`
is a query parameter. Let the browser set the multipart boundary:

```ts
const form = new FormData();
form.append("file", selectedFile);
await requestApi("/api/projects/{ident}/upload", "post", {
  path: { ident: projectId }, query: { directory: "uploads" }, body: form,
});
```

The project terminal is a real PTY, separate from API JSON:

```ts
const scheme = location.protocol === "https:" ? "wss" : "ws";
const socket = new WebSocket(`${scheme}://${location.host}/ws/projects/${encodeURIComponent(projectId)}/terminal`);
socket.onmessage = (event) => terminal.write(typeof event.data === "string" ? event.data : "");
// Forward terminal keystrokes as raw text; socket.send("\u0003") sends Ctrl-C.
terminal.onResize(({ rows, cols }) => {
  if (socket.readyState === WebSocket.OPEN)
    socket.send(JSON.stringify({ resize: { rows, cols } }));
});
// Unmount/project switch: socket.close(); terminal.dispose().
```

The terminal is rooted in the project's main branch workspace. WebSocket
authentication/origin checks are performed at connection time; policy rejection
closes with code 1008. Send keystrokes as raw text and resize controls as
`{"resize":{"rows":24,"cols":80}}`; the server recognizes the exact leading
`{"resize":` prefix and clamps both dimensions to 1–1000. Send the exact message
`{"action":"close"}` only to terminate the shared PTY session. Closing a browser
socket instead detaches that client and retains the session for reconnection.
Send resize controls when fitting the terminal canvas. Use the same-origin owner
cookie for browser connections and keep terminal traffic on the WebSocket.

## Replacing a screen

1. Preserve the screen's IDs, selection, drafts, revisions and active run IDs.
2. Load the resources in the feature map through the typed JSON adapter; keep
   blob exports, SSE and WebSockets on their appropriate transports.
3. Save drafts with their loaded revision, handle conflicts without discarding
   edits, and use returned objects to update the cache.
4. Display queued/running/paused/failed states and real artifact availability.
   Refresh on relevant events and after successful mutations.
5. Exercise the screen with concurrent edits, unavailable artifacts, empty
   collections, 401/409/422 failures, binary uploads and resumed runs before
   replacing the existing screen.

Use the repository's contract check, frontend TypeScript build and transport
tests when changing endpoint use. UI layout and component libraries can change
without changing server routes, research state or path editing semantics.
