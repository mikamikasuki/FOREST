import { test, expect } from "@playwright/test";

// Runs against the production build, normal API and actual worker. No route mocks.
test("owner creates a project, runs silent success/failure, and follows a scoped UTF-8 source", async ({ page, context, request }) => {
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "New research project", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Project name").fill(`Live observation ${Date.now()}`);
  await dialog.getByLabel("Research question").fill("Exercise actual process and source navigation");
  await dialog.getByRole("button", { name: "Create project", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/[^/]+\/workspace/);
  const projectId = new URL(page.url()).pathname.split("/")[2];
  try {
    await page.goto(`/projects/${projectId}/overview`);
    const overview = page.getByRole("region", { name: "Live project progress" });
    await expect(overview).toContainText("Narrative not enabled/configured");
    const workerPage = await context.newPage();
    await workerPage.goto(`/projects/${projectId}/workspace`);
    await workerPage.locator(".workspace-toolbar").getByRole("button", { name: "Add node", exact: true }).click();
    await workerPage.getByRole("dialog").getByLabel("Node title").fill("Silent process");
    await workerPage.getByRole("button", { name: "Create node", exact: true }).click();
    await workerPage.locator(".inspector").getByRole("tab", { name: "Config", exact: true }).click();
    const config = workerPage.getByLabel("Agent / model / tools / budget / parameters");
    for (const [command, status] of [["sleep 3", "completed"], ["sleep 3; exit 7", "failed"]]) {
      await config.fill(JSON.stringify({ kind: "command", command: ["/bin/sh", "-c", command], timeout: 15 }));
      const saveResponse = workerPage.waitForResponse(response =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/projects/${projectId}/graph/commands`));
      await workerPage.getByRole("button", { name: "Save changes", exact: true }).click();
      expect((await saveResponse).ok()).toBeTruthy();
      await workerPage.getByRole("button", { name: "Run node", exact: true }).click();
      await expect(overview.locator(".progress-counts")).toContainText("running: 1");
      try {
        await expect(overview.locator(".progress-counts")).toContainText(`${status}: 1`, { timeout: 20000 });
      } catch (error) {
        const runs = await (await request.get(`/api/projects/${projectId}/runs`)).json();
        const details = await Promise.all(runs.map(async (run: {id: string; kind: string; status: string; error: string | null}) => ({
          id: run.id, kind: run.kind, status: run.status, error: run.error,
          output: await (await request.get(`/api/runs/${run.id}/output`)).json(),
        })));
        throw new Error(`${String(error)}\nActual run failures: ${JSON.stringify(details)}`);
      }
    }
    const persisted = await (await request.get(`/api/projects/${projectId}/progress`)).json();
    expect(persisted.facts.filter((fact: { value: string; applicability: string }) => fact.value === "completed").every((fact: { applicability: string }) => fact.applicability === "historical")).toBeTruthy();
    expect((await (await request.get(`/api/projects/${projectId}/reporter-settings`)).json()).requests).toBe(0);
    await page.reload();
    await expect(overview.locator(".progress-counts")).toContainText("failed: 1");

    await page.goto(`/projects/${projectId}/files`);
    const path = "notes # ? 结果.md";
    // Source publication is a real owner file route; browser navigation is under test.
    expect((await request.put(`/api/projects/${projectId}/file`, { data: { path, content: "# A\n结果\n# A\nnegative result\n", expected_revision: 0 } })).ok()).toBeTruthy();
    const explorer = page.getByRole("region", { name: "Scoped source explorer" });
    await expect.poll(async () => explorer.getByLabel("Source scope").locator("option").allTextContents()).toContainEqual(expect.stringMatching(/^project ·/));
    const option = await explorer.getByLabel("Source scope").locator("option").filter({ hasText: /^project ·/ }).getAttribute("value");
    await explorer.getByLabel("Source scope").selectOption(option!);
    await explorer.getByRole("link", { name: path, exact: true }).click();
    await expect(explorer.locator(".source-view pre").first()).toContainText("negative result");
    await explorer.locator(".source-segments").getByRole("link", { name: /A \[2\]/ }).click();
    await expect(explorer.locator(".source-view pre").first()).toHaveText("# A\nnegative result\n");
    await page.reload();
    await expect(explorer.locator(".source-view pre").first()).toHaveText("# A\nnegative result\n");
    // Following sources and automatic observation must preserve a dirty editor.
    await page.locator('.file-tree').getByRole('button', { name: new RegExp(path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).click();
    const editor = page.locator('.file-editor .view-lines');
    await page.locator('.file-editor .monaco-editor').click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.type('unsaved owner draft');
    const saved = await (await request.get(`/api/projects/${projectId}/file?path=${encodeURIComponent(path)}`)).json();
    expect((await request.put(`/api/projects/${projectId}/file`, {data: {path, content: '# New\nchanged bytes\n', expected_revision: saved.revision}})).ok()).toBeTruthy();
    await expect(explorer.locator('.source-view')).toContainText('changed');
    await expect(editor).toHaveText('unsaved owner draft');
    await page.locator('.file-editor').getByRole('button', {name: 'Save', exact: true}).click();
    await expect(page.getByRole('dialog')).toContainText('File changed');
    await expect(editor).toHaveText('unsaved owner draft');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(explorer.getByRole("button", { name: "Recheck inventory" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
    await workerPage.close();
  } finally {
    await request.delete(`/api/projects/${projectId}`).catch(() => {});
  }
});

test('a delayed real progress response cannot cross a project switch', async ({page, request}) => {
  const a = await (await request.post('/api/projects', {data: {name: 'Delayed source A', config: {controller: {status: 'running'}}}})).json();
  const b = await (await request.post('/api/projects', {data: {name: 'Current source B', config: {controller: {status: 'paused'}}}})).json();
  let release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  let fetched!: () => void;
  const fetchedReal = new Promise<void>(resolve => {fetched = resolve;});
  let delivered!: () => void;
  const deliveredReal = new Promise<void>(resolve => {delivered = resolve;});
  // Transport fault only: fetch the actual response, then hold its delivery.
  await page.route(`**/api/projects/${a.id}/progress`, async route => {
    const response = await route.fetch(); fetched();
    await gate;
    await route.fulfill({response});
    delivered();
  });
  try {
    await page.goto(`/projects/${a.id}/overview`);
    await fetchedReal;
    await page.locator('.project-switch').click();
    await page.getByRole('button', {name: 'Current source B'}).click();
    await page.getByRole('link', {name: 'Overview', exact: true}).click();
    const progress = page.getByRole('region', {name: 'Live project progress'});
    await expect(progress).toContainText('paused');
    release();
    await deliveredReal;
    await expect(progress).not.toContainText('running');
    await expect(progress.getByRole('link', {name: 'Inspect source'}).first()).toHaveAttribute('href', new RegExp(encodeURIComponent(b.id)));
    expect((await (await request.get(`/api/projects/${b.id}/reporter-settings`)).json()).requests).toBe(0);
  } finally {
    release();
    await request.delete(`/api/projects/${a.id}`);
    await request.delete(`/api/projects/${b.id}`);
  }
});
