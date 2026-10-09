import { expect, test } from "@playwright/test";

test("Succeeded filter includes completed runs in the table and CSV export", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: { name: `Succeeded filter ${Date.now()}` },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const runId = "completed-run-visible-to-succeeded-filter";
  await page.route(`**/api/projects/${project.id}/runs`, async (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: runId,
          node_id: null,
          project_id: project.id,
          branch_id: null,
          kind: "command",
          status: "completed",
          config: {},
          node_revision: 1,
          created_at: "2026-10-01T00:00:00Z",
          started_at: "2026-10-01T00:00:00Z",
          finished_at: "2026-10-01T00:00:01Z",
          exit_code: 0,
          pid: null,
          error: null,
          metrics: { accuracy: 0.9 },
          output_path: "runs/completed-run",
        },
      ]),
    }),
  );

  try {
    await page.goto(`/projects/${project.id}/data`);
    await page.getByLabel("Filter status").selectOption({ label: "Succeeded" });
    await expect(page.getByLabel(`Select ${runId}`)).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "CSV" }).click();
    const download = await downloadPromise;
    const stream = await download.createReadStream();
    expect(stream).not.toBeNull();
    const chunks: Buffer[] = [];
    for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
    const csv = Buffer.concat(chunks).toString("utf8");
    expect(csv).toContain(runId);
    expect(csv).toContain('"completed"');
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
