import { test, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
let projectId = "";
test.beforeEach(async ({ request }) => {
  const response = await request.post("/api/projects", {
    data: {
      name: `Frontend acceptance ${Date.now()}`,
      goal: "Validate real editable workspace",
    },
  });
  expect(response.ok()).toBeTruthy();
  projectId = (await response.json()).id;
});
test.afterEach(async ({ request }) => {
  if (projectId) {
    const response = await request.delete(`/api/projects/${projectId}`);
    expect(response.ok()).toBeTruthy();
    projectId = "";
  }
});

test("queued approved actions do not offer an invalid continuation", async ({
  page,
}) => {
  let runStatus = "queued";
  let canResume = false;
  await page.route(`**/api/projects/${projectId}/decisions*`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "decision-queued",
          project_id: projectId,
          run_id: "run-queued",
          action_id: "action-1",
          attempt_id: null,
          observed_revision: 0,
          proposed: { tool: "write_file" },
          status: "accepted",
          answer: { choice: "accept" },
          answered_at: "2026-10-09T00:00:00Z",
          consumed_at: null,
          created_at: "2026-10-09T00:00:00Z",
          updated_at: "2026-10-09T00:00:00Z",
          run_status: runStatus,
          can_resume: canResume,
        },
      ]),
    }),
  );

  await page.goto(`/projects/${projectId}/overview`);
  const panel = page.getByRole("region", {
    name: "Human instructions and decisions",
  });
  await expect(panel).toContainText("Decision saved; run status: queued");
  await expect(
    panel.getByRole("button", { name: "Continue saved decision" }),
  ).toHaveCount(0);

  runStatus = "waiting_input";
  canResume = true;
  await page.evaluate(() => window.dispatchEvent(new Event("forest-refresh")));
  await expect(
    panel.getByRole("button", { name: "Continue saved decision" }),
  ).toBeVisible();
});

test("Files folders expand and collapse nested entries", async ({
  page,
  request,
}) => {
  const filePath = "records/2026/notes.md";
  const otherFilePath = "archive/summary.txt";
  const saved = await request.put(`/api/projects/${projectId}/file`, {
    data: { path: filePath, content: "Nested file contents\n" },
  });
  expect(saved.ok()).toBeTruthy();
  const otherSaved = await request.put(`/api/projects/${projectId}/file`, {
    data: { path: otherFilePath, content: "Other directory contents\n" },
  });
  expect(otherSaved.ok()).toBeTruthy();

  await page.goto(`/projects/${projectId}/files`);
  const fileTree = page.locator(".file-tree");
  const rowLabels = await fileTree.locator("button span").allTextContents();
  const archiveIndex = rowLabels.indexOf("archive");
  const recordsIndex = rowLabels.indexOf("records");
  expect(rowLabels.slice(archiveIndex, archiveIndex + 2)).toEqual([
    "archive",
    otherFilePath,
  ]);
  expect(rowLabels.slice(recordsIndex, recordsIndex + 3)).toEqual([
    "records",
    "records/2026",
    filePath,
  ]);
  const file = fileTree.getByRole("button", { name: filePath });
  const records = fileTree.getByRole("button", {
    name: "records",
    exact: true,
  });
  const year = fileTree.getByRole("button", {
    name: "records/2026",
    exact: true,
  });
  await expect(file).toBeVisible();
  await expect(records).toHaveAttribute("aria-expanded", "true");

  await records.focus();
  await page.keyboard.press("Enter");
  await expect(records).toHaveAttribute("aria-expanded", "false");
  await expect(file).toBeHidden();

  await records.click();
  await expect(file).toBeVisible();
  await year.click();
  await expect(year).toHaveAttribute("aria-expanded", "false");
  await expect(file).toBeHidden();

  await fileTree.getByPlaceholder("Find files…").fill("notes.md");
  await expect(records).toBeVisible();
  await year.click();
  await expect(file).toBeVisible();
  await file.click();
  await expect(page.locator(".monaco-editor")).toContainText(
    "Nested file contents",
  );
});

test("keyboard node movement persists its position", async ({
  page,
  request,
}) => {
  const graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const nodeId = crypto.randomUUID();
  const created = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: {
          id: nodeId,
          branch_id: graph.branches[0].id,
          type: "goal",
          title: "Keyboard position",
          position: { x: 200, y: 160 },
        },
        run: false,
      },
    },
  );
  expect(created.ok()).toBeTruthy();

  await page.goto(`/projects/${projectId}/workspace`);
  const node = page.getByTestId(`rf__node-${nodeId}`);
  await expect(node).toBeVisible();
  await node.focus();
  await page.keyboard.press("Space");
  await expect(node).toHaveClass(/selected/);
  const layoutBatches: Array<{ commands: Array<Record<string, unknown>> }> = [];
  let releaseLayoutBatch!: () => void;
  let signalLayoutBatch!: () => void;
  const layoutBatchStarted = new Promise<void>((resolve) => {
    signalLayoutBatch = resolve;
  });
  const layoutBatchGate = new Promise<void>((resolve) => {
    releaseLayoutBatch = resolve;
  });
  await page.route("**/graph/batch", async (route) => {
    signalLayoutBatch();
    await layoutBatchGate;
    await route.continue();
  });
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname.endsWith("/graph/batch") &&
      request.method() === "POST"
    ) {
      layoutBatches.push(request.postDataJSON());
    }
  });
  for (let index = 0; index < 6; index++)
    await page.keyboard.press("ArrowRight");
  await layoutBatchStarted;
  await page.getByLabel("Node title").fill("Moved and edited");
  await page.getByRole("button", { name: "Save changes" }).click();
  releaseLayoutBatch();

  await expect
    .poll(async () => {
      const current = await (
        await request.get(`/api/projects/${projectId}/graph`)
      ).json();
      return current.nodes.find((item: { id: string }) => item.id === nodeId)
        ?.position;
    })
    .toEqual({ x: 230, y: 160 });
  await expect
    .poll(async () => {
      const current = await (
        await request.get(`/api/projects/${projectId}/graph`)
      ).json();
      return current.nodes.find((item: { id: string }) => item.id === nodeId)
        ?.title;
    })
    .toBe("Moved and edited");
  expect(layoutBatches.length).toBeLessThan(6);
  expect(layoutBatches.at(-1)?.commands).toEqual([
    {
      operation: "edit_node",
      targets: [nodeId],
      params: { positions: { [nodeId]: { x: 230, y: 160 } } },
    },
  ]);

  await page.reload();
  await expect
    .poll(() =>
      node.evaluate((element) => (element as HTMLElement).style.transform),
    )
    .toBe("translate(230px, 160px)");
});

test("keyboard position saves recover from a graph revision conflict", async ({
  page,
  request,
}) => {
  const graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const nodeId = crypto.randomUUID();
  const created = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: {
          id: nodeId,
          branch_id: graph.branches[0].id,
          type: "goal",
          title: "Conflict recovery",
          position: { x: 200, y: 160 },
        },
        run: false,
      },
    },
  );
  expect(created.ok()).toBeTruthy();

  let conflictSent = false;
  const batchRevisions: number[] = [];
  await page.route("**/graph/batch", async (route) => {
    const payload = route.request().postDataJSON();
    batchRevisions.push(payload.expected_revision);
    if (conflictSent) return route.continue();
    conflictSent = true;
    const current = await (
      await request.get(`/api/projects/${projectId}/graph`)
    ).json();
    const concurrentNode = await request.post(
      `/api/projects/${projectId}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: current.revision,
          operation: "add_node",
          targets: [],
          params: {
            id: crypto.randomUUID(),
            branch_id: current.branches[0].id,
            type: "goal",
            title: "Concurrent change",
            position: { x: 520, y: 160 },
          },
          run: false,
        },
      },
    );
    expect(concurrentNode.ok()).toBeTruthy();
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: { code: "REVISION_CONFLICT", message: "Graph changed" },
      }),
    });
  });

  await page.goto(`/projects/${projectId}/workspace`);
  const node = page.getByTestId(`rf__node-${nodeId}`);
  await expect(node).toBeVisible();
  await node.focus();
  await page.keyboard.press("Space");
  await expect(node).toHaveClass(/selected/);
  await page.keyboard.press("ArrowRight");

  await expect
    .poll(async () => {
      const current = await (
        await request.get(`/api/projects/${projectId}/graph`)
      ).json();
      return current.nodes.find((item: { id: string }) => item.id === nodeId)
        ?.position;
    })
    .toEqual({ x: 205, y: 160 });
  expect(batchRevisions).toEqual([1, 2]);
  await page.reload();
  await expect
    .poll(() =>
      node.evaluate((element) => (element as HTMLElement).style.transform),
    )
    .toBe("translate(205px, 160px)");
});

test("a lost position-save response replays the original command receipt", async ({
  page,
  request,
}) => {
  let graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const nodeId = crypto.randomUUID();
  const created = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: {
          id: nodeId,
          branch_id: graph.branches[0].id,
          type: "goal",
          title: "Uncertain keyboard position",
          position: { x: 200, y: 160 },
        },
        run: false,
      },
    },
  );
  expect(created.ok()).toBeTruthy();
  graph = (await created.json()).graph;
  const submitted: Array<Record<string, any>> = [];
  let loseFirstResponse = true;
  await page.route("**/graph/batch", async (route) => {
    const payload = route.request().postDataJSON();
    submitted.push(payload);
    if (loseFirstResponse) {
      loseFirstResponse = false;
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      await response.json();
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  await page.goto(`/projects/${projectId}/workspace`);
  const node = page.getByTestId(`rf__node-${nodeId}`);
  await expect(node).toBeVisible();
  await node.focus();
  await page.keyboard.press("Space");
  await expect(node).toHaveClass(/selected/);
  for (let index = 0; index < 6; index++)
    await page.keyboard.press("ArrowRight");

  await expect
    .poll(async () => {
      const current = await (
        await request.get(`/api/projects/${projectId}/graph`)
      ).json();
      return current.nodes.find((item: { id: string }) => item.id === nodeId)
        ?.position;
    })
    .toEqual({ x: 230, y: 160 });
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1]).toEqual(submitted[0]);
  const current = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  expect(current.revision).toBe(graph.revision + 1);
});

test("discard position retries for nodes deleted by a concurrent edit", async ({
  page,
  request,
}) => {
  const graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const nodeId = crypto.randomUUID();
  const created = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: {
          id: nodeId,
          branch_id: graph.branches[0].id,
          type: "goal",
          title: "Deleted during keyboard move",
          position: { x: 200, y: 160 },
        },
        run: false,
      },
    },
  );
  expect(created.ok()).toBeTruthy();
  let batchCount = 0;
  await page.route("**/graph/batch", async (route) => {
    batchCount += 1;
    const current = await (
      await request.get(`/api/projects/${projectId}/graph`)
    ).json();
    const deleted = await request.post(
      `/api/projects/${projectId}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: current.revision,
          operation: "delete_node",
          targets: [nodeId],
          params: {},
          run: false,
        },
      },
    );
    expect(deleted.ok()).toBeTruthy();
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: { code: "REVISION_CONFLICT", message: "Graph changed" },
      }),
    });
  });

  await page.goto(`/projects/${projectId}/workspace`);
  const node = page.getByTestId(`rf__node-${nodeId}`);
  await expect(node).toBeVisible();
  await node.focus();
  await page.keyboard.press("Space");
  await expect(node).toHaveClass(/selected/);
  await page.keyboard.press("ArrowRight");
  await expect
    .poll(async () => {
      const current = await (
        await request.get(`/api/projects/${projectId}/graph`)
      ).json();
      return current.nodes.some((item: { id: string }) => item.id === nodeId);
    })
    .toBe(false);

  await page.keyboard.press(process.platform === "darwin" ? "Meta+z" : "Control+z");
  await expect
    .poll(async () => {
      const current = await (
        await request.get(`/api/projects/${projectId}/graph`)
      ).json();
      return current.nodes.some((item: { id: string }) => item.id === nodeId);
    })
    .toBe(true);
  expect(batchCount).toBe(1);
});

test("Workspace edges expose their relationship and support keyboard deletion", async ({
  page,
  request,
}) => {
  let graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const sourceId = crypto.randomUUID();
  const source = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: {
          id: sourceId,
          branch_id: graph.branches[0].id,
          type: "goal",
          title: "Source node",
          position: { x: 180, y: 160 },
        },
        run: false,
      },
    },
  );
  expect(source.ok()).toBeTruthy();
  graph = (await source.json()).graph;
  const targetId = crypto.randomUUID();
  const target = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: {
          id: targetId,
          branch_id: graph.branches[0].id,
          type: "goal",
          title: "Target node",
          position: { x: 520, y: 160 },
        },
        run: false,
      },
    },
  );
  expect(target.ok()).toBeTruthy();
  graph = (await target.json()).graph;
  const connected = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_dependency",
        targets: [],
        params: { source: sourceId, target: targetId, relation: "depends_on" },
        run: false,
      },
    },
  );
  expect(connected.ok()).toBeTruthy();
  graph = (await connected.json()).graph;
  const derived = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_dependency",
        targets: [],
        params: {
          source: sourceId,
          target: targetId,
          relation: "derived_from",
        },
        run: false,
      },
    },
  );
  expect(derived.ok()).toBeTruthy();
  graph = (await derived.json()).graph;
  const edgeId = graph.edges.find(
    (item: { relation: string }) => item.relation === "depends_on",
  ).id as string;
  const derivedEdgeId = graph.edges.find(
    (item: { relation: string }) => item.relation === "derived_from",
  ).id as string;

  await page.goto(`/projects/${projectId}/workspace`);
  const edge = page.getByTestId(`rf__edge-${edgeId}`);
  await expect(edge).toHaveAttribute(
    "aria-label",
    "Target node depends on Source node",
  );
  await expect(page.getByTestId(`rf__edge-${derivedEdgeId}`)).toHaveAttribute(
    "aria-label",
    "Target node is derived from Source node",
  );
  await edge.focus();
  await page.keyboard.press("Enter");
  await expect(edge).toHaveClass(/selected/);
  await page.keyboard.press("Space");
  await expect(edge).toHaveClass(/selected/);

  await page.route(`**/api/projects/${projectId}/graph`, async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Refresh unavailable" }),
    });
  });
  await page.keyboard.press("Delete");

  await expect
    .poll(async () => {
      const current = await (
        await request.get(`/api/projects/${projectId}/graph`)
      ).json();
      return current.edges.some((item: { id: string }) => item.id === edgeId);
    })
    .toBe(false);
  await expect(edge).toHaveCount(0);
  await expect(page.getByTestId(`rf__node-${sourceId}`)).toBeVisible();
  await expect(page.getByTestId(`rf__node-${targetId}`)).toBeVisible();
  await expect(page.getByTestId(`rf__edge-${derivedEdgeId}`)).toBeVisible();
  await expect(page.getByText("No nodes")).toHaveCount(0);

  await page.unroute(`**/api/projects/${projectId}/graph`);
  await page.reload();
  await expect(edge).toHaveCount(0);
  await expect(page.getByTestId(`rf__edge-${derivedEdgeId}`)).toBeVisible();
});

test("filtering an edge off the canvas prevents its keyboard deletion", async ({
  page,
  request,
}) => {
  let graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const addNode = async (title: string, x: number) => {
    const result = await request.post(
      `/api/projects/${projectId}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: graph.revision,
          operation: "add_node",
          targets: [],
          params: {
            id: crypto.randomUUID(),
            branch_id: graph.branches[0].id,
            type: "goal",
            title,
            position: { x, y: 160 },
          },
          run: false,
        },
      },
    );
    expect(result.ok()).toBeTruthy();
    graph = (await result.json()).graph;
    return graph.nodes.find((node: { title: string }) => node.title === title);
  };
  const source = await addNode("Visible source", 180);
  const target = await addNode("Hidden target", 520);
  const connected = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_dependency",
        targets: [],
        params: {
          source: source.id,
          target: target.id,
          relation: "depends_on",
        },
        run: false,
      },
    },
  );
  expect(connected.ok()).toBeTruthy();
  graph = (await connected.json()).graph;
  const edgeId = graph.edges.find(
    (edge: { source: string; target: string }) =>
      edge.source === source.id && edge.target === target.id,
  ).id as string;
  const forked = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "fork_branch",
        targets: [target.id],
        params: {
          name: "Filtered edge branch",
          copy_policy: { code: true, data: "reference", results: false },
        },
        run: false,
      },
    },
  );
  expect(forked.ok()).toBeTruthy();
  graph = (await forked.json()).graph;
  const alternate = graph.branches.find(
    (branch: { id: string }) => branch.id !== source.branch_id,
  );

  await page.goto(`/projects/${projectId}/workspace`);
  const edge = page.getByTestId(`rf__edge-${edgeId}`);
  await expect(edge).toBeVisible();
  await edge.focus();
  await page.keyboard.press("Enter");
  await expect(edge).toHaveClass(/selected/);
  await page.getByLabel("Current branch").selectOption(alternate.id);
  await expect(edge).toHaveCount(0);
  await page.locator(".react-flow").press("Delete");
  await page.getByLabel("Current branch").selectOption("all");
  await expect(edge).toBeVisible();
  await expect(edge).not.toHaveClass(/selected/);

  const current = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  expect(current.edges.some((item: { id: string }) => item.id === edgeId)).toBe(
    true,
  );
});

test("a stale editor cannot overwrite a file recreated after deletion", async ({
  page,
  request,
}) => {
  const path = "notes/revision-aba.txt";
  const initial = await request.put(`/api/projects/${projectId}/file`, {
    data: { path, content: "original bytes\n", expected_revision: 0 },
  });
  expect(initial.ok()).toBeTruthy();
  expect((await initial.json()).revision).toBe(1);

  await page.goto(`/projects/${projectId}/files`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByRole("button", { name: path }).click();
  const editor = page.locator(".monaco-editor").first();
  await expect(editor).toContainText("original bytes");
  await editor.click();
  await page.keyboard.press(
    process.platform === "darwin" ? "Meta+A" : "Control+A",
  );
  await page.keyboard.insertText("stale editor bytes\n");

  const deleted = await request.delete(`/api/projects/${projectId}/file`, {
    params: { path },
  });
  expect(deleted.ok()).toBeTruthy();
  const recreated = await request.put(`/api/projects/${projectId}/file`, {
    data: { path, content: "new bytes\n" },
  });
  expect(recreated.ok()).toBeTruthy();
  expect((await recreated.json()).revision).toBe(3);

  const staleSave = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/projects/${projectId}/file` &&
      response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect((await staleSave).status()).toBe(409);
  await expect(
    page.getByText("File changed elsewhere", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".modal pre")).toContainText("new bytes");
  await expect(editor).toContainText("stale editor bytes");
  const readback = await (
    await request.get(`/api/projects/${projectId}/file`, { params: { path } })
  ).json();
  expect(readback).toMatchObject({
    path,
    content: "new bytes\n",
    revision: 3,
  });
});

test("Files page navigates bounded file-list pages", async ({ page }) => {
  const firstCursor = JSON.stringify({ is_dir: false, path: "alpha.txt" });
  await page.route(`**/api/projects/${projectId}/files*`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== `/api/projects/${projectId}/files`)
      return route.continue();
    const cursor = url.searchParams.get("cursor");
    await route.fulfill({
      json: cursor
        ? {
            files: [
              { path: "zeta.txt", size: 1, modified: 0, is_dir: false },
            ],
            has_more: false,
            next_cursor: null,
          }
        : {
            files: [
              { path: "alpha.txt", size: 1, modified: 0, is_dir: false },
            ],
            has_more: true,
            next_cursor: firstCursor,
          },
    });
  });

  await page.goto(`/projects/${projectId}/files`);
  const tree = page.locator(".file-tree");
  await expect(tree).toContainText("alpha.txt");
  await expect(tree).not.toContainText("zeta.txt");
  await expect(tree.locator(".file-tree-pagination")).toContainText(
    "Search covers this page only",
  );
  await tree
    .locator(".file-tree-pagination")
    .getByRole("button", { name: "Next", exact: true })
    .click();
  await expect(tree).toContainText("zeta.txt");
  await expect(tree).not.toContainText("alpha.txt");
  await expect(tree.locator(".file-tree-pagination")).toContainText("Page 2");
});

test("Files page discards an in-flight page after switching projects", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: { name: `Pagination target ${Date.now()}`, goal: "Route race" },
  });
  expect(created.ok()).toBeTruthy();
  const otherProjectId = (await created.json()).id as string;
  try {
    await page.route(`**/api/projects/${projectId}/files*`, async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.has("cursor")) {
        await new Promise((resolve) => setTimeout(resolve, 300));
        await route.fulfill({
          json: {
            files: [
              { path: "stale-project-a.txt", size: 1, modified: 0, is_dir: false },
            ],
            has_more: false,
            next_cursor: null,
          },
        });
      } else {
        await route.fulfill({
          json: {
            files: [
              { path: "project-a-first.txt", size: 1, modified: 0, is_dir: false },
            ],
            has_more: true,
            next_cursor: "opaque-cursor-for-a",
          },
        });
      }
    });
    await page.route(`**/api/projects/${otherProjectId}/files*`, (route) =>
      route.fulfill({
        json: {
          files: [
            { path: "project-b-file.txt", size: 1, modified: 0, is_dir: false },
          ],
          has_more: false,
          next_cursor: null,
        },
      }),
    );

    await page.goto(`/projects/${projectId}/files`);
    await page.locator(".file-tree-pagination").getByRole("button", { name: "Next" }).click();
    await page.evaluate((target) => {
      window.history.pushState({}, "", target);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }, `/projects/${otherProjectId}/files`);
    const tree = page.locator(".file-tree");
    await expect(tree).toContainText("project-b-file.txt");
    await page.waitForTimeout(400);
    await expect(tree).not.toContainText("stale-project-a.txt");
  } finally {
    await request.delete(`/api/projects/${otherProjectId}`);
  }
});

test("Files upload preflight detects conflicts outside the visible page", async ({
  page,
}) => {
  let overwrite: string | null = null;
  page.on("dialog", (dialog) => dialog.accept());
  await page.route(`**/api/projects/${projectId}/files?limit=500`, (route) =>
    route.fulfill({
      json: {
        files: [
          { path: "visible.txt", size: 1, modified: 0, is_dir: false },
        ],
        has_more: false,
        next_cursor: null,
      },
    }),
  );
  await page.route(`**/api/projects/${projectId}/files/existing`, (route) =>
    route.fulfill({
      json: { existing: ["uploads/remote.txt"], directories: [] },
    }),
  );
  await page.route(`**/api/projects/${projectId}/upload*`, async (route) => {
    overwrite = new URL(route.request().url()).searchParams.get("overwrite");
    await route.fulfill({ json: { path: "uploads/remote.txt", size: 3 } });
  });
  await page.goto(`/projects/${projectId}/files`);
  await page.locator('input[type="file"]').setInputFiles({
    name: "remote.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("new"),
  });
  await expect.poll(() => overwrite).toBe("true");
});

test("the newest CSV text read wins when preview toggles overlap", async ({
  page,
  request,
}) => {
  const path = "measurements.csv";
  const saved = await request.put(`/api/projects/${projectId}/file`, {
    data: { path, content: "value\nsaved\n", expected_revision: 0 },
  });
  expect(saved.ok()).toBeTruthy();

  let readCount = 0;
  const started: Array<() => void> = [];
  const releases: Array<(content: string) => void> = [];
  await page.route(
    `**/api/projects/${projectId}/file?path=*`,
    async (route) => {
      const index = readCount++;
      started[index]?.();
      const content = await new Promise<string>((resolve) => {
        releases[index] = resolve;
      });
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ path, content, revision: index + 2 }),
      });
    },
  );
  const firstStarted = new Promise<void>((resolve) => {
    started[0] = resolve;
  });
  const secondStarted = new Promise<void>((resolve) => {
    started[1] = resolve;
  });

  await page.goto(`/projects/${projectId}/files`);
  await page.locator(".file-tree").getByRole("button", { name: path }).click();
  const toggle = page.getByRole("button", { name: "Toggle saved table preview" });
  await toggle.click();
  await firstStarted;
  await toggle.click();
  await secondStarted;

  releases[1]("value\nnewest read\n");
  const editor = page.locator(".monaco-editor").first();
  await expect(editor).toContainText("newest read");
  releases[0]("value\nolder read\n");
  await expect(editor).toContainText("newest read");
  await expect(editor).not.toContainText("older read");
});

test("a failed CSV text read shows an error with a retry action", async ({
  page,
  request,
}) => {
  const path = "measurements.csv";
  const saved = await request.put(`/api/projects/${projectId}/file`, {
    data: { path, content: "value\nsaved\n", expected_revision: 0 },
  });
  expect(saved.ok()).toBeTruthy();

  await page.goto(`/projects/${projectId}/files`);
  await page.locator(".file-tree").getByRole("button", { name: path }).click();
  await page.route(`**/api/projects/${projectId}/file?path=*`, (route) =>
    route.fulfill({ status: 503, body: "temporarily unavailable" }),
  );
  await page.getByRole("button", { name: "Toggle saved table preview" }).click();
  await expect(page.locator(".error-box")).toContainText("Service Unavailable");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

for (const scenario of [
  {
    name: "silent success",
    command: "sleep 2",
    status: "completed",
    exitCode: 0,
  },
  {
    name: "failure traceback progress",
    command: "sleep 2; exit 7",
    status: "failed",
    exitCode: 1,
  },
  {
    name: "stdout progress",
    command: "echo workspace-progress; sleep 2",
    status: "completed",
    exitCode: 0,
  },
]) {
  test(`mounted Workspace refreshes after ${scenario.name}`, async ({
    page,
    request,
  }) => {
    const graph = await (
      await request.get(`/api/projects/${projectId}/graph`)
    ).json();
    const nodeId = crypto.randomUUID();
    const created = await request.post(
      `/api/projects/${projectId}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: graph.revision,
          operation: "add_node",
          targets: [],
          params: {
            id: nodeId,
            branch_id: graph.branches[0].id,
            type: "experiment",
            title: `Workspace ${scenario.name}`,
            config: {
              kind: "command",
              command: ["/bin/sh", "-c", scenario.command],
              timeout: 10,
            },
          },
          run: false,
        },
      },
    );
    expect(created.ok()).toBeTruthy();
    await page.goto(`/projects/${projectId}/workspace`);
    await page.locator(`.react-flow__node[data-id="${nodeId}"]`).click();
    await page.getByRole("button", { name: "Run node", exact: true }).click();
    const panel = page.locator(".run-panel");
    await expect(panel.locator(".run-list .badge")).toHaveText("running");
    await expect(page.locator(".topbar-actions")).toContainText("1 tasks");
    await expect(
      panel.getByRole("button", { name: "cancel", exact: true }),
    ).toBeEnabled();
    if (scenario.name === "stdout progress") {
      await expect(panel.locator("pre")).toContainText("workspace-progress");
    }
    if (scenario.name === "failure traceback progress") {
      await expect(panel.locator("pre")).toContainText(
        "Command exited with code 7",
      );
    }
    await expect
      .poll(
        async () => {
          const runs = await (
            await request.get(`/api/projects/${projectId}/runs`)
          ).json();
          expect(runs).toHaveLength(1);
          expect(runs[0].node_id).toBe(nodeId);
          return { status: runs[0].status, exitCode: runs[0].exit_code };
        },
        { timeout: 20000 },
      )
      .toEqual({ status: scenario.status, exitCode: scenario.exitCode });

    // Keep the same page mounted: reloading would hide the missing terminal listener.
    await expect(panel.locator(".run-list .badge")).toHaveText(scenario.status);
    await expect(panel.locator(".run-output-toolbar")).toContainText(
      `exit ${scenario.exitCode}`,
    );
    await expect(panel.locator(".run-list > button")).toHaveCount(1);
    await expect(page.locator(".topbar-actions")).not.toContainText("1 tasks");
    for (const control of ["pause", "resume", "cancel"]) {
      await expect(
        panel.getByRole("button", { name: control, exact: true }),
      ).toBeDisabled();
    }
    await expect(
      panel.getByRole("button", { name: "retry", exact: true }),
    ).toBeEnabled();
  });
}

test("RunPanel shows an Agent final summary alongside its output log", async ({
  page,
}) => {
  const runId = "agent-final-summary-run";
  await page.route(`**/api/projects/${projectId}/runs?limit=500`, (route) =>
    route.fulfill({
      json: [
        {
          id: runId,
          node_id: null,
          project_id: projectId,
          branch_id: null,
          kind: "agent",
          status: "completed",
          config: {},
          node_revision: 0,
          created_at: new Date().toISOString(),
          started_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
          exit_code: 0,
          pid: null,
          error: null,
          metrics: { summary: "The Agent's final answer is ready." },
          output_path: "",
        },
      ],
    }),
  );
  await page.route(`**/api/runs/${runId}/output*`, (route) =>
    route.fulfill({ json: { text: "Tool response trace", offset: 19 } }),
  );
  await page.goto(`/projects/${projectId}/workspace`);

  const panel = page.locator(".run-panel");
  await expect(panel.locator(".run-final-summary")).toContainText(
    "The Agent's final answer is ready.",
  );
  await expect(panel.locator("pre")).toContainText("Tool response trace");
});

test("graph updates remain stable and persist a node created through the inspector UI", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`/projects/${projectId}/workspace`);
  await page
    .locator(".workspace-toolbar")
    .getByRole("button", { name: "Add node", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByLabel("Node title")
    .fill("Real frontend acceptance node");
  await page
    .getByRole("dialog")
    .getByLabel("Instructions")
    .fill("Inspect actual saved configuration");
  await page.getByRole("button", { name: "Create node", exact: true }).click();
  await expect(page.locator(".research-node")).toHaveCount(1);
  await expect(page.getByLabel("Node title")).toHaveValue(
    "Real frontend acceptance node",
  );
  await page.getByLabel("Node title").fill("Edited through inspector");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.locator(".research-node h3")).toHaveText(
    "Edited through inspector",
  );
  await page.reload();
  await expect(page.locator(".research-node h3")).toHaveText(
    "Edited through inspector",
  );
  const graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  expect(graph.nodes[0].title).toBe("Edited through inspector");
  expect(errors).toEqual([]);
});

test("a stale editor cannot recreate an untracked file after another tab renames it", async ({
  page,
  request,
}) => {
  const dataDir = process.env.FOREST_DATA_DIR;
  if (!dataDir)
    throw new Error("FOREST_DATA_DIR is required for this browser test");
  const oldPath = "outputs/untracked-result.txt";
  const newPath = "outputs/renamed-result.txt";
  const originalContent = "executor result\n";
  const diskPath = join(dataDir, "projects", projectId, oldPath);
  await mkdir(join(dataDir, "projects", projectId, "outputs"), {
    recursive: true,
  });
  await writeFile(diskPath, originalContent);

  const opened = await request.get(
    `/api/projects/${projectId}/file?path=${encodeURIComponent(oldPath)}`,
  );
  expect(await opened.json()).toMatchObject({
    content: originalContent,
    revision: 0,
    origin: "executor_or_import",
  });

  await page.goto(`/projects/${projectId}/files`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByTitle(oldPath).click();
  await expect(page.locator(".file-toolbar")).toContainText(oldPath);
  await page.locator(".monaco-editor").click();
  await page.keyboard.press(
    process.platform === "darwin" ? "Meta+A" : "Control+A",
  );
  await page.keyboard.insertText("stale edit from tab one\n");

  const secondTab = await page.context().newPage();
  await secondTab.goto(`/projects/${projectId}/files`, {
    waitUntil: "domcontentloaded",
  });
  await secondTab.getByTitle(oldPath).click();
  secondTab.once("dialog", (dialog) => dialog.accept(newPath));
  await secondTab.getByRole("button", { name: "Rename" }).click();
  await expect(secondTab.locator(".file-toolbar")).toContainText(newPath);

  const saveResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "PUT" &&
      response.url().includes(`/api/projects/${projectId}/file`),
  );
  await page.getByRole("button", { name: "Save" }).click();
  expect((await saveResponse).status()).toBe(409);
  const recoveryPath = "outputs/recovered-result.txt";
  const missingFileConflict = page.getByRole("dialog", {
    name: "File deleted or renamed",
  });
  await expect(missingFileConflict).toBeVisible();
  await expect(
    missingFileConflict.getByRole("button", { name: "Save as" }),
  ).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept(recoveryPath));
  await missingFileConflict.getByRole("button", { name: "Save as" }).click();
  await expect(page.locator(".file-toolbar")).toContainText(recoveryPath);
  await page.locator(".monaco-editor").click();
  await page.keyboard.press(
    process.platform === "darwin" ? "Meta+A" : "Control+A",
  );
  await page.keyboard.insertText("updated recovered draft\n");
  const recoveredSave = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/projects/${projectId}/file` &&
      response.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect((await recoveredSave).status()).toBe(200);

  const stalePath = await request.get(
    `/api/projects/${projectId}/file?path=${encodeURIComponent(oldPath)}`,
  );
  const renamedPath = await request.get(
    `/api/projects/${projectId}/file?path=${encodeURIComponent(newPath)}`,
  );
  expect(stalePath.status()).toBe(404);
  expect(await renamedPath.json()).toMatchObject({
    content: originalContent,
    revision: 0,
  });
  const recovered = await request.get(
    `/api/projects/${projectId}/file?path=${encodeURIComponent(recoveryPath)}`,
  );
  expect(await recovered.json()).toMatchObject({
    path: recoveryPath,
    content: "updated recovered draft\n",
    revision: 2,
    origin: "user_edited",
  });
  await secondTab.close();
});

test("Overview reload and continue preserve the saved autonomous-planning setting", async ({
  page,
  request,
}) => {
  await page.goto(`/projects/${projectId}/overview`);
  const autonomous = page.getByRole("checkbox", {
    name: "Autonomous planning",
  });
  await expect(autonomous).toBeChecked();
  await autonomous.uncheck();
  await page.getByRole("button", { name: "Start / continue" }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/projects/${projectId}/research`)
          ).json()
        ).controller.autonomous,
    )
    .toBe(false);

  await page.reload();
  await expect(autonomous).not.toBeChecked();
  await page.getByRole("button", { name: "Start / continue" }).click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/projects/${projectId}/research`)
          ).json()
        ).controller.autonomous,
    )
    .toBe(false);
  await expect
    .poll(async () => {
      const state = await (
        await request.get(`/api/projects/${projectId}/research`)
      ).json();
      return {
        autonomous: state.controller.autonomous,
        runs: state.counts.runs,
      };
    })
    .toEqual({ autonomous: false, runs: 0 });
});

test("another editor changing the node presents a conflict without overwriting server content", async ({
  page,
  request,
}) => {
  let graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const created = await (
    await request.post(`/api/projects/${projectId}/graph/commands`, {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: { title: "Original question", type: "goal" },
        run: false,
      },
    })
  ).json();
  const node = created.graph.nodes[0];
  await page.goto(`/projects/${projectId}/workspace`);
  await expect(page.getByLabel("Node title")).toHaveValue("Original question");
  await page.getByLabel("Node title").fill("Local draft");
  graph = await (await request.get(`/api/projects/${projectId}/graph`)).json();
  await request.post(`/api/projects/${projectId}/graph/commands`, {
    data: {
      request_id: crypto.randomUUID(),
      expected_revision: graph.revision,
      operation: "edit_node",
      targets: [node.id],
      params: { title: "Other editor" },
      run: false,
    },
  });
  await expect(page.locator(".research-node h3")).toHaveText("Other editor");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Node changed elsewhere" }),
  ).toBeVisible();
  const persisted = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  expect(persisted.nodes[0].title).toBe("Other editor");
  await page
    .getByRole("button", { name: "Load latest content", exact: true })
    .click();
  await expect(page.getByLabel("Node title")).toHaveValue("Other editor");
});

test("saved CSV preview reads real file rows and paginates without altering the file", async ({
  page,
  request,
}) => {
  const content =
    "name,value\n" +
    Array.from({ length: 60 }, (_, index) => `seed${index},${index * 2}`).join(
      "\n",
    );
  const path = "qa/data.csv";
  const response = await request.put(
    `/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`,
    { data: { path, content } },
  );
  expect(response.ok()).toBeTruthy();
  await page.goto(`/projects/${projectId}/files`);
  await page.getByTitle(path, { exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "seed49", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "seed59", exact: true }),
  ).toBeVisible();
  const persisted = await (
    await request.get(
      `/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`,
    )
  ).json();
  expect(persisted.content).toBe(content);
});

test("guided demonstration follows ordinary project pages without automatically executing research", async ({
  page,
  request,
}) => {
  const writes: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/api/"))
      writes.push(req.url());
  });
  await page.goto(`/projects/${projectId}/overview?demo=1&demo_step=A`);
  await expect(
    page.getByRole("region", { name: "Project walkthrough" }),
  ).toBeVisible();
  await expect(page.locator(".demo-steps a")).toHaveCount(9);
  await page.locator(".demo-steps a").nth(1).click();
  await expect(page).toHaveURL(
    new RegExp(`/projects/${projectId}/workspace\\?demo=1&demo_step=B`),
  );
  await page
    .locator(".sidebar nav a")
    .filter({ hasText: "Data & analysis" })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/projects/${projectId}/data\\?demo=1`),
  );
  await expect(
    page.getByRole("region", { name: "Project walkthrough" }),
  ).toBeVisible();
  expect(writes).toEqual([]);
  expect(
    await (await request.get(`/api/projects/${projectId}/runs`)).json(),
  ).toEqual([]);
  await page
    .getByRole("button", { name: "Exit walkthrough", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Project walkthrough" }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/data$`));
});

test("search reveals an offscreen node in a large graph and reopens its inspector", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const initial = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const ids = Array.from({ length: 1000 }, () => crypto.randomUUID());
  const title = "Final large graph navigation target";
  let seedRevision = initial.revision;
  for (let start = 0; start < ids.length; start += 200) {
    const seeded = await request.post(
      `/api/projects/${projectId}/graph/batch`,
      {
        timeout: 60000,
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: seedRevision,
          commands: ids.slice(start, start + 200).map((id, offset) => {
            const index = start + offset;
            return {
              operation: "add_node",
              targets: [],
              params: {
                id,
                type: "implementation",
                title: index === 999 ? title : `Navigation node ${index}`,
                position: {
                  x: (index % 20) * 300,
                  y: Math.floor(index / 20) * 220,
                },
              },
            };
          }),
        },
      },
    );
    expect(seeded.ok()).toBeTruthy();
    seedRevision = (await seeded.json()).graph.revision;
  }
  const before = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  await page.goto(`/projects/${projectId}/workspace`);
  await expect(page.locator(".research-node").first()).toBeVisible();
  await page
    .getByRole("button", { name: "Close inspector", exact: true })
    .click();
  await expect(page.locator(".inspector")).toHaveCount(0);
  await page.getByLabel("Search nodes").fill(title);
  await page
    .locator(".node-search-results")
    .getByRole("button", { name: title, exact: true })
    .click();
  await expect(page.locator(".inspector")).toBeVisible();
  await expect(page.getByLabel("Node title")).toHaveValue(title);
  const target = page.locator(`.react-flow__node[data-id="${ids[999]}"]`);
  await expect(target).toBeInViewport({ ratio: 0.8 });
  await expect(target.locator(".research-node")).toHaveClass(/selected/);
  const after = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  expect(after.revision).toBe(before.revision);
  expect(after.nodes).toEqual(before.nodes);
});

test("search reveals nodes hidden by the current branch or a collapsed ancestor", async ({
  page,
  request,
}) => {
  let graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const command = async (
    operation: string,
    targets: string[],
    params: object,
  ) => {
    const response = await request.post(
      `/api/projects/${projectId}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: graph.revision,
          operation,
          targets,
          params,
          run: false,
        },
      },
    );
    expect(response.ok()).toBeTruthy();
    graph = (await response.json()).graph;
  };
  await command("add_node", [], {
    title: "Navigation parent",
    type: "goal",
    position: { x: 0, y: 0 },
  });
  const parent = graph.nodes[0];
  await command("fork_branch", [parent.id], {
    name: "Navigation alternative",
    copy_policy: { code: true, data: "reference", results: false },
  });
  const child = graph.nodes.find(
    (node: { id: string }) => node.id !== parent.id,
  );
  const title = "Filtered navigation target";
  await command("edit_node", [child.id], {
    title,
    position: { x: 6000, y: 6000 },
  });
  graph = await (await request.get(`/api/projects/${projectId}/graph`)).json();
  const revision = graph.revision;
  await page.goto(`/projects/${projectId}/workspace`);
  await page.getByLabel("Current branch").selectOption(parent.branch_id);
  const target = page.locator(`.react-flow__node[data-id="${child.id}"]`);
  await expect(target).toHaveCount(0);
  const find = async (name: string) => {
    await page.getByLabel("Search nodes").fill(name);
    await page
      .locator(".node-search-results")
      .getByRole("button", { name, exact: true })
      .click();
  };
  await find(title);
  await expect(page.getByLabel("Current branch")).toHaveValue("all");
  await expect(page.getByLabel("Node title")).toHaveValue(title);
  await expect(target).toBeInViewport({ ratio: 0.8 });
  await find("Navigation parent");
  await page.getByRole("button", { name: "Collapse", exact: true }).click();
  await expect(target).toHaveCount(0);
  await find(title);
  await expect(target).toBeInViewport({ ratio: 0.8 });
  await expect(page.getByLabel("Node title")).toHaveValue(title);
  const after = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  expect(after.revision).toBe(revision);
  expect(after.nodes).toEqual(graph.nodes);
});

test("narrow workspace opens a dismissible inspector and keeps the searched node visible after closing", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  await page.setViewportSize({ width: 786, height: 800 });
  const initial = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const ids = Array.from({ length: 1000 }, () => crypto.randomUUID());
  const title = "Narrow workspace final navigation target";
  let seedRevision = initial.revision;
  for (let start = 0; start < ids.length; start += 200) {
    const seeded = await request.post(
      `/api/projects/${projectId}/graph/batch`,
      {
        timeout: 60000,
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: seedRevision,
          commands: ids.slice(start, start + 200).map((id, offset) => {
            const index = start + offset;
            return {
              operation: "add_node",
              targets: [],
              params: {
                id,
                type: "implementation",
                title:
                  index === 999 ? title : `Narrow navigation node ${index}`,
                position: {
                  x: (index % 20) * 300,
                  y: Math.floor(index / 20) * 220,
                },
              },
            };
          }),
        },
      },
    );
    expect(seeded.ok()).toBeTruthy();
    seedRevision = (await seeded.json()).graph.revision;
  }
  const before = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  await page.goto(`/projects/${projectId}/workspace`);
  await expect(page.locator(".research-node").first()).toBeVisible();
  const toggle = page.getByRole("button", {
    name: "Toggle inspector",
    exact: true,
  });
  const inspector = page.getByRole("complementary", { name: "Node inspector" });
  await expect(inspector).toHaveCount(0);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  const canvasBefore = await page.locator(".canvas-region").boundingBox();
  await toggle.click();
  await expect(inspector).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const panel = await inspector.boundingBox();
  const canvasOpen = await page.locator(".canvas-region").boundingBox();
  expect(panel!.width).toBeLessThanOrEqual(360);
  expect(panel!.x).toBeGreaterThan(canvasOpen!.x);
  expect(canvasOpen!.width).toBe(canvasBefore!.width);
  await inspector.getByRole("button", { name: "Close inspector" }).click();
  await expect(inspector).toHaveCount(0);
  await page.getByLabel("Search nodes").fill(title);
  await page
    .locator(".node-search-results")
    .getByRole("button", { name: title, exact: true })
    .click();
  await expect(inspector).toBeVisible();
  await expect(inspector.getByLabel("Node title")).toHaveValue(title);
  await expect(inspector.getByLabel("Node title")).toBeEditable();
  await expect(
    inspector.getByRole("button", { name: "Close inspector" }),
  ).toBeInViewport();
  await inspector.getByRole("button", { name: "Close inspector" }).click();
  const target = page.locator(`.react-flow__node[data-id="${ids[999]}"]`);
  await expect(target).toBeInViewport({ ratio: 0.8 });
  await expect(target.locator(".research-node")).toHaveClass(/selected/);
  await expect
    .poll(async () =>
      target.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return document
          .elementFromPoint(
            bounds.x + bounds.width / 2,
            bounds.y + bounds.height / 2,
          )
          ?.closest(".react-flow__node")
          ?.getAttribute("data-id");
      }),
    )
    .toBe(ids[999]);
  await toggle.click();
  await expect(inspector.getByLabel("Node title")).toHaveValue(title);
  await inspector.getByRole("button", { name: "Close inspector" }).click();
  const after = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  expect(after.revision).toBe(before.revision);
  expect(after.nodes).toEqual(before.nodes);
});

test("Paper full-manuscript picker lists completed runs from this project", async ({
  page,
  request,
}) => {
  const budget = await request.patch(`/api/projects/${projectId}`, {
    data: { budget: { max_runs: 5, seconds: 120, allow_paid: false } },
  });
  expect(budget.ok()).toBeTruthy();

  const graph = await (
    await request.get(`/api/projects/${projectId}/graph`)
  ).json();
  const nodeId = crypto.randomUUID();
  const command = await request.post(
    `/api/projects/${projectId}/graph/commands`,
    {
      data: {
        request_id: crypto.randomUUID(),
        expected_revision: graph.revision,
        operation: "add_node",
        targets: [],
        params: {
          id: nodeId,
          branch_id: graph.branches[0].id,
          type: "experiment",
          title: "Paper run picker numeric metrics",
          config: {
            kind: "command",
            command: [
              "/usr/bin/python3",
              "-c",
              "import json; from pathlib import Path; Path('metrics.json').write_text(json.dumps({'score': 7, 'observations': 3}))",
            ],
            timeout: 20,
          },
        },
      },
    },
  );
  expect(command.ok()).toBeTruthy();

  const started = await request.post(`/api/nodes/${nodeId}/run`, {
    data: { request_id: crypto.randomUUID(), scope: "single" },
  });
  expect(started.ok()).toBeTruthy();
  const queued = await started.json();
  let completedRun: any;
  await expect
    .poll(
      async () => {
        const current = await request.get(`/api/runs/${queued.id}`);
        completedRun = await current.json();
        return completedRun.status;
      },
      { timeout: 30000 },
    )
    .toBe("completed");
  expect(completedRun.exit_code).toBe(0);

  const runsPath = `/api/projects/${projectId}/runs`;
  const evidenceResponse = await request.get(runsPath, {
    params: { include_manuscript_evidence: "true" },
  });
  expect(evidenceResponse.status()).toBe(200);
  const runs = await evidenceResponse.json();
  const eligibleRun = runs.find((run: { id: string }) => run.id === queued.id);
  expect(eligibleRun).toMatchObject({
    id: queued.id,
    project_id: projectId,
    status: "completed",
    kind: "command",
    manuscript_evidence: {
      ready: true,
      numeric_measurements: 2,
    },
  });
  expect(eligibleRun.manuscript_evidence.metrics_file).toBeTruthy();

  const metricsPath = `${eligibleRun.output_path}/${eligibleRun.manuscript_evidence.metrics_file}`;
  const metricsResponse = await request.get(`/api/projects/${projectId}/file`, {
    params: { path: metricsPath },
  });
  expect(metricsResponse.status()).toBe(200);
  const metricsArtifact = await metricsResponse.json();
  expect(JSON.parse(metricsArtifact.content)).toEqual({
    score: 7,
    observations: 3,
  });

  const waitForProjectRuns = () =>
    page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === runsPath &&
        url.searchParams.get("include_manuscript_evidence") === "true"
      );
    });
  const initialRunsResponse = waitForProjectRuns();
  await page.goto(`/projects/${projectId}/paper`);
  const initialResponse = await initialRunsResponse;
  expect(initialResponse.status()).toBe(200);
  expect(
    (await initialResponse.json()).every(
      (run: { project_id: string }) => run.project_id === projectId,
    ),
  ).toBe(true);

  const picker = page
    .locator("details.paper-layout")
    .filter({ hasText: "Generate a full manuscript from actual experiments" });
  await picker
    .getByText("Generate a full manuscript from actual experiments", {
      exact: true,
    })
    .click();
  const runLabel = `command 路 ${queued.id.slice(0, 8)}`;
  const runCheckbox = picker.getByRole("checkbox", { name: runLabel });
  const generate = picker.getByRole("button", {
    name: "Generate full manuscript",
    exact: true,
  });
  await expect(runCheckbox).toBeVisible();
  await expect(generate).toBeDisabled();
  await runCheckbox.check();
  await expect(generate).toBeEnabled();

  const reloadedRunsResponse = waitForProjectRuns();
  await page.reload();
  const reloadedResponse = await reloadedRunsResponse;
  expect(reloadedResponse.status()).toBe(200);
  const reloadedRuns = await reloadedResponse.json();
  expect(
    reloadedRuns.find((run: { id: string }) => run.id === queued.id),
  ).toMatchObject({
    project_id: projectId,
    manuscript_evidence: { ready: true },
  });
  await picker
    .getByText("Generate a full manuscript from actual experiments", {
      exact: true,
    })
    .click();
  await expect(picker.getByRole("checkbox", { name: runLabel })).toBeVisible();
  await expect(generate).toBeDisabled();
});

test("Paper full-manuscript picker keeps the empty-project control", async ({
  page,
}) => {
  const runsPath = `/api/projects/${projectId}/runs`;
  const evidenceResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === runsPath &&
      url.searchParams.get("include_manuscript_evidence") === "true"
    );
  });
  await page.goto(`/projects/${projectId}/paper`);
  const evidenceResponse = await evidenceResponsePromise;
  expect(evidenceResponse.status()).toBe(200);
  expect(await evidenceResponse.json()).toEqual([]);

  const picker = page
    .locator("details.paper-layout")
    .filter({ hasText: "Generate a full manuscript from actual experiments" });
  await picker
    .getByText("Generate a full manuscript from actual experiments", {
      exact: true,
    })
    .click();

  await expect(
    picker.getByText(
      "Complete an experiment that produces readable numeric metrics.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(picker.getByRole("checkbox")).toHaveCount(0);
  await expect(
    picker.getByRole("button", {
      name: "Generate full manuscript",
      exact: true,
    }),
  ).toBeDisabled();
});
