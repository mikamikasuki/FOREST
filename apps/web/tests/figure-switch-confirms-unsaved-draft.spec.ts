import { expect, test } from "@playwright/test";

test("switching figures warns before discarding unsaved edits", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: { name: `Figure draft guard ${Date.now()}` },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const first = await request.post("/api/figures", {
    data: {
      project_id: project.id,
      title: "First draft target",
      data: { kind: "bar", caption: "Saved first caption", style: {} },
    },
  });
  const second = await request.post("/api/figures", {
    data: {
      project_id: project.id,
      title: "Second draft target",
      data: { kind: "bar", caption: "Saved second caption", style: {} },
    },
  });
  expect(first.ok()).toBeTruthy();
  expect(second.ok()).toBeTruthy();

  try {
    await page.goto(`/projects/${project.id}/figures`);
    const firstFigure = page
      .locator(".figure-selector button")
      .filter({ hasText: "First draft target" });
    const secondFigure = page
      .locator(".figure-selector button")
      .filter({ hasText: "Second draft target" });
    await firstFigure.click();
    const caption = page.getByLabel("Caption");
    await expect(caption).toHaveValue("Saved first caption");
    await caption.fill("Unsaved caption edit");

    const cancelPrompt = async () => {
      const dialogPromise = page.waitForEvent("dialog");
      const clickPromise = secondFigure.click();
      const dialog = await dialogPromise;
      expect(dialog.message()).toContain("Discard unsaved figure changes?");
      await dialog.dismiss();
      await clickPromise;
    };
    await cancelPrompt();
    await expect(caption).toHaveValue("Unsaved caption edit");

    const acceptPrompt = async () => {
      const dialogPromise = page.waitForEvent("dialog");
      const clickPromise = secondFigure.click();
      const dialog = await dialogPromise;
      await dialog.accept();
      await clickPromise;
    };
    await acceptPrompt();
    await expect(caption).toHaveValue("Saved second caption");
    await firstFigure.click();
    await expect(caption).toHaveValue("Saved first caption");
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
