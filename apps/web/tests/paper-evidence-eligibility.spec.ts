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

  async function runCommand(
    title: string,
    source: string,
    projectId = project.id as string,
  ) {
    const graph = await (
      await request.get(`/api/projects/${projectId}/graph`)
    ).json();
    const nodeId = crypto.randomUUID();
    const node = await request.post(
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

  let foreignProjectId: string | null = null;
  try {
    const withoutMetrics = await runCommand(
      "Completed command without metrics",
      "print('completed without metrics')",
    );
    const nonnumericMetrics = await runCommand(
      "Completed command with nonnumeric metrics",
      "from pathlib import Path; Path('metrics.json').write_text('{\"note\":\"no numeric measurements\"}')",
    );
    const oversizedMetrics = await runCommand(
      "Completed command with an oversized integer metric",
      "from pathlib import Path; Path('metrics.json').write_text('{\"measurement\":' + '9' * 400 + '}')",
    );
    const runsPath = `/api/projects/${project.id}/runs`;
    const waitForProjectRuns = () =>
      page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname === runsPath &&
          url.searchParams.get("include_manuscript_evidence") === "true"
        );
      });
    const ineligibleResponsePromise = waitForProjectRuns();
    await page.goto(`/projects/${project.id}/paper`);
    const ineligibleResponse = await ineligibleResponsePromise;
    expect(ineligibleResponse.status()).toBe(200);
    const ineligibleRuns = await ineligibleResponse.json();
    for (const runId of [withoutMetrics, nonnumericMetrics, oversizedMetrics]) {
      expect(
        ineligibleRuns.find((run: { id: string }) => run.id === runId),
      ).toMatchObject({
        status: "completed",
        manuscript_evidence: { ready: false },
      });
    }
    await page
      .getByText("Generate a full manuscript from actual experiments")
      .click();
    const picker = page
      .locator("details.paper-layout")
      .filter({ hasText: "Generate a full manuscript from actual experiments" });
    for (const runId of [withoutMetrics, nonnumericMetrics, oversizedMetrics]) {
      await expect(
        picker.getByText(`command · ${runId.slice(0, 8)}`, { exact: true }),
      ).toHaveCount(0);
    }
    await expect(
      picker.getByText(
        "Complete an experiment that produces readable numeric metrics.",
        { exact: true },
      ),
    ).toBeVisible();
    const generate = page.getByRole("button", {
      name: "Generate full manuscript",
    });

    const withMetrics = await runCommand(
      "Completed arithmetic command with metrics",
      "import json; from pathlib import Path; total=sum(range(100)); Path('metrics.json').write_text(json.dumps({'sum': total})); print(total)",
    );
    const foreignCreated = await request.post("/api/projects", {
      data: {
        name: `Other paper project ${Date.now()}`,
        goal: "Keep this local metric out of the selected project's picker.",
        budget: { max_runs: 2, seconds: 120, allow_paid: false },
      },
    });
    expect(foreignCreated.ok()).toBeTruthy();
    const foreignProject = await foreignCreated.json();
    foreignProjectId = foreignProject.id;
    const foreignRun = await runCommand(
      "Completed numeric run in another project",
      "from pathlib import Path; Path('metrics.json').write_text('{\"other_project\": 99}')",
      foreignProject.id,
    );

    const currentRunsResponse = await request.get(runsPath, {
      params: { include_manuscript_evidence: "true" },
    });
    expect(currentRunsResponse.status()).toBe(200);
    const currentRuns = await currentRunsResponse.json();
    const eligibleRun = currentRuns.find(
      (run: { id: string }) => run.id === withMetrics,
    );
    expect(eligibleRun).toMatchObject({
      project_id: project.id,
      status: "completed",
      kind: "command",
      manuscript_evidence: {
        ready: true,
        numeric_measurements: 1,
      },
    });
    expect(
      currentRuns.some((run: { id: string }) => run.id === foreignRun),
    ).toBe(false);
    const metricsPath = `${eligibleRun.output_path}/${eligibleRun.manuscript_evidence.metrics_file}`;
    const artifactResponse = await request.get(
      `/api/projects/${project.id}/file`,
      { params: { path: metricsPath } },
    );
    expect(artifactResponse.status()).toBe(200);
    const artifact = await artifactResponse.json();
    expect(JSON.parse(artifact.content)).toEqual({ sum: 4950 });

    const eligibleResponsePromise = waitForProjectRuns();
    await page.reload();
    const eligibleResponse = await eligibleResponsePromise;
    expect(eligibleResponse.status()).toBe(200);
    const eligibleRuns = await eligibleResponse.json();
    expect(
      eligibleRuns.find((run: { id: string }) => run.id === withMetrics),
    ).toMatchObject({ manuscript_evidence: { ready: true } });
    await expect(
      picker.getByText(`command · ${foreignRun.slice(0, 8)}`, { exact: true }),
    ).toHaveCount(0);
    await picker
      .getByText("Generate a full manuscript from actual experiments", {
        exact: true,
      })
      .click();
    const eligibleRunLabel = `command · ${withMetrics.slice(0, 8)}`;
    const eligibleRunOption = picker.getByText(eligibleRunLabel, {
      exact: true,
    });
    await expect(eligibleRunOption).toBeVisible();
    await expect(generate).toBeDisabled();
    await eligibleRunOption.locator("..").locator("input").check();
    await expect(generate).toBeEnabled();
  } finally {
    if (foreignProjectId) {
      const deletedForeignProject = await request.delete(
        `/api/projects/${foreignProjectId}`,
      );
      expect(deletedForeignProject.ok()).toBeTruthy();
    }
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});
