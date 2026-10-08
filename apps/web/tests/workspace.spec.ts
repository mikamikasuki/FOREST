import { test, expect } from "@playwright/test";
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
