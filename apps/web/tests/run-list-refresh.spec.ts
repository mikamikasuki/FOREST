import { expect, test } from "@playwright/test";

test("Data and Figure Studio refresh run statuses on project updates", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Run list refresh ${Date.now()}`,
      goal: "Refresh mounted run selectors after project updates.",
      budget: { max_runs: 5, seconds: 120, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const runId = crypto.randomUUID();
  const run = {
    id: runId,
    project_id: project.id,
    kind: "command",
    status: "queued",
    metrics: {},
  };
  const figureCreated = await request.post("/api/figures", {
    data: {
      project_id: project.id,
      title: "Refresh fixture",
      data: {
        kind: "bar",
        style: {},
        code: "",
        run_ids: [],
        metric: "accuracy",
        data: {},
        caption: "",
        outputs: {},
      },
    },
  });
  expect(figureCreated.ok()).toBeTruthy();

  let status = "queued";
  await page.route(`**/api/projects/${project.id}/runs*`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([{ ...run, status }]),
    });
  });

  try {
    await page.goto(`/projects/${project.id}/data`);
    const row = page.getByRole("row").filter({ hasText: runId.slice(0, 8) });
    await expect(row).toContainText("queued");

    status = "completed";
    const dataRefresh = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/projects/${project.id}/runs`),
    );
    await page.evaluate(() => window.dispatchEvent(new Event("forest-refresh")));
    await dataRefresh;
    await expect(row).toContainText("completed");

    status = "running";
    await page.goto(`/projects/${project.id}/figures`);
    await page.getByRole("tab", { name: "Data" }).click();
    const boundRuns = page.getByLabel("Bound runs");
    const runOption = boundRuns.locator("option").filter({
      hasText: runId.slice(0, 8),
    });
    await expect(runOption).toContainText("running");

    status = "completed";
    const figureRefresh = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/projects/${project.id}/runs`),
    );
    await page.evaluate(() => window.dispatchEvent(new Event("forest-refresh")));
    await figureRefresh;
    await expect(runOption).toContainText("completed");
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
