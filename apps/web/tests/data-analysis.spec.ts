import { expect, test } from "@playwright/test";

test("recomputing analysis ignores repeated clicks while the request is pending", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("/api/projects", {
      data: {
        name: `Analysis dedup ${Date.now()}`,
        goal: "Avoid duplicate work",
      },
    })
  ).json();
  const run = {
    id: "completed-run",
    project_id: project.id,
    node_id: null,
    branch_id: null,
    kind: "command",
    status: "completed",
    config: {},
    node_revision: 1,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    exit_code: 0,
    pid: null,
    error: null,
    metrics: { accuracy: 0.9 },
    output_path: "outputs/completed-run",
  };
  let attempts = 0;
  await page.route(`**/api/projects/${project.id}/runs`, (route) =>
    route.fulfill({ json: [run] }),
  );
  await page.route("**/api/analyses?**", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/api/analysis/run", async (route) => {
    attempts += 1;
    await new Promise((resolve) => setTimeout(resolve, 400));
    if (attempts === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Temporary analysis failure" }),
      });
      return;
    }
    await route.fulfill({ status: 202, json: { run_id: "analysis-run" } });
  });

  try {
    await page.goto(`/projects/${project.id}/data`);
    const button = page.getByRole("button", { name: "Recompute statistics" });
    await expect(button).toBeEnabled();

    await button.click();
    await expect(button).toBeDisabled();
    await button.dispatchEvent("click");
    expect(attempts).toBe(1);
    await expect(page.getByText("Temporary analysis failure")).toBeVisible();
    expect(attempts).toBe(1);
    await expect(button).toBeEnabled();

    await button.click();
    await expect(button).toBeDisabled();
    await expect(button).toBeEnabled();
    expect(attempts).toBe(2);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});
