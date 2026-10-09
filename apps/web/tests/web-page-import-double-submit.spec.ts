import { expect, test } from "@playwright/test";

test("a repeated in-flight web-page import submits only once", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: { name: `Web import guard ${Date.now()}` },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  let importCalls = 0;
  let requestStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    requestStarted = resolve;
  });
  let releaseRequest!: () => void;
  const requestGate = new Promise<void>((resolve) => {
    releaseRequest = resolve;
  });
  await page.route("**/api/browser/read", async (route) => {
    importCalls += 1;
    if (importCalls === 1) {
      requestStarted();
      await requestGate;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: `web-source-${importCalls}`,
        title: "Example page",
        url: "https://example.com/",
        text_path: "library/page-example/page.txt",
      }),
    });
  });

  try {
    await page.goto(`/projects/${project.id}/library`);
    await page.getByText("Import a web page", { exact: true }).click();
    await page.getByLabel("Public page URL").fill("https://example.com/");
    const save = page.getByRole("button", { name: "Read & save" });
    await save.click();
    await started;

    await save.evaluate((button) =>
      button.dispatchEvent(new MouseEvent("click", { bubbles: true })),
    );
    expect(importCalls).toBe(1);
    await expect(save).toBeDisabled();

    releaseRequest();
    await expect(save).toBeEnabled();
    await save.click();
    await expect.poll(() => importCalls).toBe(2);
  } finally {
    releaseRequest();
    await request.delete(`/api/projects/${project.id}`);
  }
});
