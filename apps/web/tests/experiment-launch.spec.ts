import { expect, test } from "@playwright/test";

test("experiment launch ignores repeated clicks until its request finishes", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("/api/projects", {
      data: {
        name: `Experiment launch dedup ${Date.now()}`,
        goal: "Avoid duplicate experiment runs",
      },
    })
  ).json();
  const experiment = {
    id: "experiment-dedup",
    project_id: project.id,
    title: "Double-click regression",
    revision: 1,
    status: "ready",
    data: {
      duty: "Check repeated launch handling",
      seeds: [1],
      budget: { seconds: 10 },
      command: "test",
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  let attempts = 0;
  await page.route("**/api/experiments?**", (route) =>
    route.fulfill({ json: [experiment] }),
  );
  await page.route(`**/api/projects/${project.id}/runs`, (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route(
    `**/api/experiments/${experiment.id}/launch`,
    async (route) => {
      attempts += 1;
      await new Promise((resolve) => setTimeout(resolve, 400));
      if (attempts === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Temporary launch failure" }),
        });
        return;
      }
      await route.fulfill({ status: 202, json: { id: "queued-run" } });
    },
  );

  try {
    await page.goto(`/projects/${project.id}/experiments`);
    const launch = page.getByRole("button", { name: "Launch" });
    await expect(launch).toBeEnabled();

    await launch.click();
    await expect(launch).toBeDisabled();
    await launch.dispatchEvent("click");
    expect(attempts).toBe(1);
    await expect(page.getByText("Temporary launch failure")).toBeVisible();
    await expect(launch).toBeEnabled();
    expect(attempts).toBe(1);

    await launch.click();
    await expect(launch).toBeDisabled();
    await expect(launch).toBeEnabled();
    expect(attempts).toBe(2);
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});
