import { expect, test } from "@playwright/test";

test("Paper only offers completed runs with actual numeric metrics", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Paper evidence eligibility ${Date.now()}`,
      goal: "Use completed local computation as manuscript evidence.",
      budget: { max_runs: 5, seconds: 120, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  async function runCommand(title: string, source: string) {
    const graph = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const nodeId = crypto.randomUUID();
    const node = await request.post(
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
            title,
            config: {
              kind: "command",
              command: ["/usr/bin/python3", "-c", source],
              timeout: 20,
            },
          },
        },
      },
    );
    expect(node.ok()).toBeTruthy();
    const started = await request.post(`/api/nodes/${nodeId}/run`, {
      data: { request_id: crypto.randomUUID(), scope: "single" },
    });
    expect(started.ok()).toBeTruthy();
    const run = await started.json();
    await expect
      .poll(async () => {
        const current = await request.get(`/api/runs/${run.id}`);
        return (await current.json()).status;
      })
      .toBe("completed");
    return run.id as string;
  }

  try {
    const withoutMetrics = await runCommand(
      "Completed command without metrics",
      "print('completed without metrics')",
    );
    const withMetrics = await runCommand(
      "Completed arithmetic command with metrics",
      "import json; from pathlib import Path; total=sum(range(100)); Path('metrics.json').write_text(json.dumps({'sum': total})); print(total)",
    );

    const eligibilityRequest = page.waitForRequest((candidate) =>
      candidate
        .url()
        .includes(
          `/api/projects/${project.id}/runs?include_manuscript_evidence=true`,
        ),
    );
    await page.goto(`/projects/${project.id}/paper`);
    await eligibilityRequest;
    await page
      .getByText("Generate a full manuscript from actual experiments")
      .click();

    await expect(
      page.getByText(`command · ${withoutMetrics.slice(0, 8)}`, {
        exact: true,
      }),
    ).toHaveCount(0);
    const eligibleRun = page.getByText(`command · ${withMetrics.slice(0, 8)}`, {
      exact: true,
    });
    await expect(eligibleRun).toBeVisible();
    const generate = page.getByRole("button", {
      name: "Generate full manuscript",
    });
    await expect(generate).toBeDisabled();
    await eligibleRun.locator("..").locator("input").check();
    await expect(generate).toBeEnabled();
  } finally {
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});
