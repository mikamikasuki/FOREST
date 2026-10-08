import { expect, test } from "@playwright/test";

test("Paper generation lists completed runs from the current project", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Paper generation ${Date.now()}`,
      goal: "Select a completed experiment for manuscript generation.",
      budget: { max_runs: 4, seconds: 30, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  try {
    const graph = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const nodeId = crypto.randomUUID();
    const command = await request.post(
      `/api/projects/${project.id}/graph/commands`,
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
            title: "Paper evidence run",
            config: {
              kind: "command",
              command: ["/bin/sh", "-c", "sleep 1; printf paper-evidence"],
              timeout: 10,
            },
          },
          run: false,
        },
      },
    );
    expect(command.ok()).toBeTruthy();

    await page.goto(`/projects/${project.id}/workspace`);
    await page.locator(`.react-flow__node[data-id="${nodeId}"]`).click();
    await page.getByRole("button", { name: "Run node", exact: true }).click();
    await expect
      .poll(
        async () => {
          const runs = await (
            await request.get(`/api/projects/${project.id}/runs`)
          ).json();
          return runs.some(
            (candidate: { node_id: string; status: string }) =>
              candidate.node_id === nodeId && candidate.status === "completed",
          );
        },
        { timeout: 20000 },
      )
      .toBeTruthy();
    const runs = await (
      await request.get(`/api/projects/${project.id}/runs`)
    ).json();
    const run = runs.find(
      (candidate: { node_id: string; status: string }) =>
        candidate.node_id === nodeId && candidate.status === "completed",
    );
    expect(run).toBeDefined();

    const runsResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/projects/${project.id}/runs`) &&
        response.request().method() === "GET",
    );
    await page.goto(`/projects/${project.id}/paper`);
    expect((await runsResponse).ok()).toBeTruthy();
    await page
      .getByText("Generate a full manuscript from actual experiments", {
        exact: true,
      })
      .click();

    if (!run) {
      throw new Error("Completed command run was not returned by the API");
    }
    const runLabel = `command · ${run.id.slice(0, 8)}`;
    const runCheckbox = page
      .locator("label")
      .filter({ hasText: runLabel })
      .getByRole("checkbox");
    await expect(runCheckbox).toBeVisible();
    await runCheckbox.check();
    await expect(
      page.getByRole("button", {
        name: "Generate full manuscript",
        exact: true,
      }),
    ).toBeEnabled();
  } finally {
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});
