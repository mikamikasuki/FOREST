import { afterEach, describe, it, expect, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { api, ApiError, download, formatDate, hasCycle } from "./api";
import { apiUrl, requestApi } from "./apiClient";
import type { Graph } from "./api";
import { ErrorBox, Empty, Button } from "./ui";
const graph = (edges: Graph["edges"]): Graph => ({
  project_id: "p",
  revision: 1,
  nodes: [],
  branches: [],
  edges,
});
describe("research dependency validation", () => {
  it("rejects an indirect cycle through execution dependencies", () => {
    expect(
      hasCycle(
        graph([
          { id: "1", source: "a", target: "b", relation: "depends_on" },
          { id: "2", source: "b", target: "c", relation: "consumes" },
        ]),
        "c",
        "a",
      ),
    ).toBe(true);
  });
  it("ignores reference and history edges when checking execution cycles", () => {
    expect(
      hasCycle(
        graph([
          { id: "1", source: "a", target: "b", relation: "history" },
          { id: "2", source: "b", target: "c", relation: "evidence" },
        ]),
        "c",
        "a",
      ),
    ).toBe(false);
  });
  it("rejects a self-dependency and terminates on an existing cycle", () => {
    expect(hasCycle(graph([]), "a", "a")).toBe(true);
    expect(
      hasCycle(
        graph([
          { id: "1", source: "a", target: "b", relation: "depends_on" },
          { id: "2", source: "b", target: "a", relation: "consumes" },
        ]),
        "z",
        "a",
      ),
    ).toBe(false);
  });
});
describe("empty and failed workspace components", () => {
  it("renders recovery control and actual error text", () => {
    const html = renderToStaticMarkup(
      <ErrorBox error="Worker unavailable" retry={() => {}} />,
    );
    expect(html).toContain("Worker unavailable");
    expect(html).toContain("Retry");
    expect(html).toContain("<button");
  });
  it("renders empty state without numerical evidence", () => {
    const html = renderToStaticMarkup(
      <Empty
        title="No measured results"
        description="Execute an experiment to collect data."
      />,
    );
    expect(html).toContain("No measured results");
    expect(html).toContain("Execute an experiment");
    expect(html).not.toContain("100%");
  });
});

describe("submission controls", () => {
  it("blocks another submission while busy even when the ordinary disabled predicate is false", () => {
    expect(
      renderToStaticMarkup(
        <Button busy disabled={false}>
          Run
        </Button>,
      ),
    ).toContain('disabled=""');
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("API transport compatibility", () => {
  it("retains same-origin JSON requests and accepts both existing path forms", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"id":"p"}'));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api("/projects", "POST", { name: "Study" })).toEqual({
      id: "p",
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/projects", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: '{"name":"Study"}',
    });
    fetchMock.mockResolvedValue(new Response("[]"));
    await api("/api/projects");
    expect(fetchMock).toHaveBeenLastCalledWith("/api/projects", {
      method: "GET",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: undefined,
    });
  });
  it("passes multipart fields intact and leaves the boundary to the browser", async () => {
    const form = new FormData();
    form.append("file", new Blob(["actual source"]), "source.txt");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('{"path":"uploads/source.txt"}'));
    vi.stubGlobal("fetch", fetchMock);
    await api("/projects/p/upload", "POST", form);
    expect(fetchMock).toHaveBeenCalledWith("/api/projects/p/upload", {
      method: "POST",
      credentials: "same-origin",
      headers: {},
      body: form,
    });
  });
  it("distinguishes an omitted body from explicit JSON null and accepts 204", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api("/records/r", "DELETE")).toBeUndefined();
    await api("/records/r", "PATCH", null);
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
    expect(fetchMock.mock.calls[1][1].body).toBe("null");
  });
  it("preserves revision conflict codes, messages and recovery suggestions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "REVISION_CONFLICT",
              message: "Project changed",
              suggestion: "Reload and merge changes.",
            },
          }),
          { status: 409 },
        ),
      ),
    );
    await expect(
      api("/projects/p", "PATCH", { expected_revision: 3 }),
    ).rejects.toMatchObject({
      status: 409,
      code: "REVISION_CONFLICT",
      message: "Project changed",
      suggestion: "Reload and merge changes.",
    });
  });
  it("dispatches the existing authentication event once and never retries a mutation", async () => {
    const dispatchEvent = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response('{"detail":{"code":"UNAUTHORIZED","message":"Sign in"}}', {
          status: 401,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("window", { dispatchEvent });
    await expect(
      api("/nodes/n/run", "POST", { request_id: "same-operation" }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(dispatchEvent).toHaveBeenCalledTimes(1);
    expect(dispatchEvent.mock.calls[0][0].type).toBe("forest-auth-required");
  });
  it("keeps FastAPI validation arrays and plain-text failure fallback readable", async () => {
    const detail = [
      { loc: ["body", "name"], msg: "Field required", type: "missing" },
    ];
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail }), { status: 422 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("/projects", "POST", {})).rejects.toMatchObject({
      status: 422,
      code: "request_failed",
      message: JSON.stringify(detail),
    });
    fetchMock.mockResolvedValue(
      new Response("Gateway unavailable", {
        status: 502,
        statusText: "Bad Gateway",
      }),
    );
    await expect(api("/projects")).rejects.toMatchObject({
      status: 502,
      message: "Bad Gateway",
    });
  });
  it("surfaces network and malformed JSON failures without a second submission", async () => {
    const network = new TypeError("Failed to fetch");
    const fetchMock = vi.fn().mockRejectedValue(network);
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("/projects", "POST", { name: "Study" })).rejects.toBe(
      network,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock.mockResolvedValue(new Response("broken json"));
    await expect(api("/projects")).rejects.toBeInstanceOf(SyntaxError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("generated-contract JSON client", () => {
  it("escapes route values and retains falsy query values while omitting absent values", () => {
    expect(
      apiUrl("/api/projects/{ident}/file", {
        path: { ident: "project /?#" },
        query: {
          path: "runs/a & b.txt",
          offset: 0,
          descending: false,
          unset: undefined,
          empty: "",
          missing: null,
        },
      }),
    ).toBe(
      "/api/projects/project%20%2F%3F%23/file?path=runs%2Fa+%26+b.txt&offset=0&descending=false&empty=",
    );
    expect(apiUrl("/api/example", { query: { tag: ["a", "b"] } })).toBe(
      "/api/example?tag=a&tag=b",
    );
    expect(() => apiUrl("/api/projects/{ident}/graph")).toThrow(
      "Missing API path parameter: ident",
    );
  });
  it("calls the original transport with generated path/query shapes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);
    await requestApi("/api/projects", "get", {
      query: { limit: 2, archived: false },
    });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/projects?limit=2&archived=false",
    );
    fetchMock.mockResolvedValue(
      new Response(
        '{"project_id":"p","revision":0,"nodes":[],"edges":[],"branches":[]}',
      ),
    );
    await requestApi("/api/projects/{ident}/graph", "get", {
      path: { ident: "p" },
    });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/projects/p/graph");
  });
  it("passes typed multipart uploads through without serializing their file", async () => {
    const form = new FormData();
    form.append("file", new Blob(["value\n1\n"]), "observations.csv");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('{"path":"uploads/observations.csv"}'));
    vi.stubGlobal("fetch", fetchMock);
    await requestApi("/api/projects/{ident}/upload", "post", {
      path: { ident: "p" },
      query: { directory: "uploads" },
      body: form,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects/p/upload?directory=uploads",
      {
        method: "POST",
        credentials: "same-origin",
        headers: {},
        body: form,
      },
    );
  });
  it("keeps the nullable run date presentation unchanged", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
  });
});

describe("binary download compatibility", () => {
  it("preserves GET/POST exports, filename priority and delayed object URL cleanup", async () => {
    vi.useFakeTimers();
    const anchor = { href: "", download: "", click: vi.fn() };
    vi.stubGlobal("document", {
      createElement: vi.fn().mockReturnValue(anchor),
    });
    const createUrl = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:forest-test");
    const revokeUrl = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("saved export", {
        headers: { "content-disposition": 'attachment; filename="paper.pdf"' },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await download("/papers/p/export", { format: "pdf", expected_revision: 4 });
    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/papers/p/export",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: '{"format":"pdf","expected_revision":4}',
      },
    ]);
    expect(createUrl).toHaveBeenCalledTimes(1);
    expect(anchor.download).toBe("paper.pdf");
    expect(anchor.click).toHaveBeenCalledTimes(1);
    expect(revokeUrl).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(revokeUrl).toHaveBeenCalledWith("blob:forest-test");
    fetchMock.mockResolvedValue(new Response("source"));
    await download(
      "/projects/p/download?path=source.txt",
      undefined,
      "my-source.txt",
    );
    expect(fetchMock.mock.calls[1][1].method).toBe("GET");
    expect(anchor.download).toBe("my-source.txt");
  });
  it("rejects export conflicts before constructing a download", async () => {
    const createUrl = vi.spyOn(URL, "createObjectURL");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            '{"detail":{"code":"STALE_PDF","message":"Compile the current revision"}}',
            { status: 409 },
          ),
        ),
    );
    await expect(
      download("/papers/p/export", { format: "pdf" }),
    ).rejects.toMatchObject({ status: 409, code: "STALE_PDF" });
    expect(createUrl).not.toHaveBeenCalled();
  });
});

// These calls are checked by tsc, never sent. A changed contract must update the adapter explicitly.
function contractTypeChecks() {
  // @ts-expect-error Only documented routes are accepted.
  requestApi("/api/unknown-route", "get");
  // @ts-expect-error Graph reads require the documented path identity.
  requestApi("/api/projects/{ident}/graph", "get");
  // @ts-expect-error GET projects does not accept a request body.
  requestApi("/api/projects", "get", { body: { name: "Study" } });
  // @ts-expect-error No DELETE operation exists for the projects collection.
  requestApi("/api/projects", "delete");
  // @ts-expect-error Project events are an SSE stream, not a JSON response.
  requestApi("/api/projects/{ident}/events", "get", { path: { ident: "p" } });
  // @ts-expect-error Manuscript export is a binary download, not a JSON response.
  requestApi("/api/papers/{ident}/export", "post", {
    path: { ident: "p" },
    body: {},
  });
  requestApi("/api/projects/{ident}/upload", "post", {
    path: { ident: "p" },
    // @ts-expect-error Multipart upload bodies must be FormData.
    body: { file: "source.csv" },
  });
}
