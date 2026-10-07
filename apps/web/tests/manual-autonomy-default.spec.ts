import { expect, test } from "@playwright/test";

test("a new Manual project defaults autonomy off until explicitly enabled", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Manual autonomy default ${Date.now()}`,
      goal: "Verify that an unset autonomy preference follows Manual run mode.",
      mode: "manual",
      budget: { max_runs: 8, seconds: 60, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  try {
    const initial = await (
      await request.get(`/api/projects/${project.id}/research`)
    ).json();
    expect(initial.controller?.autonomous).toBeUndefined();

    await page.goto(`/projects/${project.id}/overview`);
    const autonomous = page.getByRole("checkbox", {
      name: "Autonomous planning",
    });
    await expect(autonomous).not.toBeChecked();

    const startRequest = page.waitForRequest(
      (candidate) =>
        candidate.method() === "POST" &&
        candidate.url().endsWith(`/api/projects/${project.id}/research/start`),
    );
    await page.getByRole("button", { name: "Start / continue" }).click();
    const requestSent = await startRequest;
    expect(requestSent.postDataJSON().autonomous).toBe(false);

    await expect
      .poll(async () => {
        const state = await (
          await request.get(`/api/projects/${project.id}/research`)
        ).json();
        return {
          autonomous: state.controller?.autonomous,
          runs: state.counts?.runs,
        };
      })
      .toEqual({ autonomous: false, runs: 0 });

    await autonomous.check();
    const optInRequest = page.waitForRequest(
      (candidate) =>
        candidate.method() === "POST" &&
        candidate.url().endsWith(`/api/projects/${project.id}/research/start`),
    );
    await page.getByRole("button", { name: "Start / continue" }).click();
    const explicitRequest = await optInRequest;
    expect(explicitRequest.postDataJSON().autonomous).toBe(true);

    await expect
      .poll(async () => {
        const state = await (
          await request.get(`/api/projects/${project.id}/research`)
        ).json();
        return {
          autonomous: state.controller?.autonomous,
          runs: state.counts?.runs,
        };
      })
      .toEqual({ autonomous: true, runs: 0 });
  } finally {
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});

test("switching an unset Assisted project to Manual turns autonomy off", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Manual autonomy mode change ${Date.now()}`,
      goal: "Verify the default follows a saved run-mode change.",
      mode: "assisted",
      budget: { max_runs: 8, seconds: 60, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  try {
    const initial = await (
      await request.get(`/api/projects/${project.id}/research`)
    ).json();
    expect(initial.controller?.autonomous).toBeUndefined();

    await page.goto(`/projects/${project.id}/overview`);
    const autonomous = page.getByRole("checkbox", {
      name: "Autonomous planning",
    });
    await expect(autonomous).toBeChecked();
    await page.getByLabel("Run mode").selectOption("manual");
    await page.getByRole("button", { name: "Save controls" }).click();

    await expect(autonomous).not.toBeChecked();
    const savedProject = await (
      await request.get(`/api/projects/${project.id}`)
    ).json();
    expect(savedProject.mode).toBe("manual");

    const startRequest = page.waitForRequest(
      (candidate) =>
        candidate.method() === "POST" &&
        candidate.url().endsWith(`/api/projects/${project.id}/research/start`),
    );
    await page.getByRole("button", { name: "Start / continue" }).click();
    const requestSent = await startRequest;
    expect(requestSent.postDataJSON().autonomous).toBe(false);

    await expect
      .poll(async () => {
        const state = await (
          await request.get(`/api/projects/${project.id}/research`)
        ).json();
        return {
          autonomous: state.controller?.autonomous,
          runs: state.counts?.runs,
        };
      })
      .toEqual({ autonomous: false, runs: 0 });
  } finally {
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});

test("an explicitly saved Manual autonomy setting remains enabled on Overview load", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Manual autonomy saved true ${Date.now()}`,
      goal: "Verify a saved Manual autonomy choice remains authoritative.",
      mode: "manual",
      budget: { max_runs: 8, seconds: 60, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  try {
    const started = await request.post(
      `/api/projects/${project.id}/research/start`,
      { data: { branch_id: null, autonomous: true } },
    );
    expect(started.ok()).toBeTruthy();
    const state = await (
      await request.get(`/api/projects/${project.id}/research`)
    ).json();
    expect(state.controller?.autonomous).toBe(true);
    expect(state.counts?.runs).toBe(0);

    await page.route(`**/api/projects/${project.id}/research`, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.continue();
    });
    await page.goto(`/projects/${project.id}/overview`);
    const autonomous = page.getByRole("checkbox", {
      name: "Autonomous planning",
    });
    await expect(autonomous).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Start / continue" }),
    ).toBeDisabled();
    await expect(autonomous).toBeChecked();
    await expect(autonomous).toBeEnabled();
  } finally {
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});

test("a failed research-state load keeps autonomy controls disabled", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Manual autonomy load failure ${Date.now()}`,
      goal: "Verify a failed state read cannot replace a saved autonomy choice.",
      mode: "manual",
      budget: { max_runs: 8, seconds: 60, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  try {
    const started = await request.post(
      `/api/projects/${project.id}/research/start`,
      { data: { branch_id: null, autonomous: true } },
    );
    expect(started.ok()).toBeTruthy();

    await page.route(`**/api/projects/${project.id}/research`, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Temporary research-state failure" }),
      }),
    );
    await page.goto(`/projects/${project.id}/overview`);

    await expect(page.getByRole("alert")).toContainText(
      "Unable to load saved controller settings",
    );
    await expect(
      page.getByRole("checkbox", { name: "Autonomous planning" }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Start / continue" }),
    ).toBeDisabled();

    const persisted = await (
      await request.get(`/api/projects/${project.id}/research`)
    ).json();
    expect(persisted.controller?.autonomous).toBe(true);
  } finally {
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});
