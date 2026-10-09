import { expect, test } from "@playwright/test";

test("a repeated in-flight Duplicate click creates only one copy", async ({
  page,
  request,
}) => {
  const name = `Duplicate guard ${Date.now()}`;
  const created = await request.post("/api/projects", {
    data: { name, goal: "", description: "" },
  });
  expect(created.ok()).toBeTruthy();
  const source = await created.json();

  let duplicateCalls = 0;
  let requestStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    requestStarted = resolve;
  });
  let releaseRequest!: () => void;
  const requestGate = new Promise<void>((resolve) => {
    releaseRequest = resolve;
  });
  await page.route(`**/api/projects/${source.id}/duplicate`, async (route) => {
    duplicateCalls += 1;
    requestStarted();
    await requestGate;
    await route.fulfill({ response: await route.fetch() });
  });

  try {
    await page.goto("/");
    const card = page.locator(".project-card").filter({
      has: page.getByRole("button", { name, exact: true }),
    });
    const duplicate = card.getByRole("button", {
      name: "Duplicate",
      exact: true,
    });
    await expect(duplicate).toBeEnabled();
    await duplicate.click();
    await started;

    await duplicate.evaluate((button) =>
      button.dispatchEvent(new MouseEvent("click", { bubbles: true })),
    );
    expect(duplicateCalls).toBe(1);
    await expect(duplicate).toBeDisabled();

    releaseRequest();
    await expect(duplicate).toBeEnabled();
    await duplicate.click();
    await expect.poll(() => duplicateCalls).toBe(2);
  } finally {
    releaseRequest();
    const projectsResponse = await request.get("/api/projects");
    if (projectsResponse.ok()) {
      const projects = await projectsResponse.json();
      for (const project of projects.filter((item: { name: string }) =>
        item.name === name || item.name === `${name} · Copy`,
      )) {
        await request.delete(`/api/projects/${project.id}`);
      }
    }
  }
});
