import { expect, test } from "@playwright/test";

test("switching from saved CSV preview back to editing keeps the unsaved draft", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: { name: `CSV draft preview ${Date.now()}` },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const path = "observations.csv";
  const original = "value\noriginal\n";
  const saved = await request.put(`/api/projects/${project.id}/file`, {
    data: { path, content: original, expected_revision: 0 },
  });
  expect(saved.ok()).toBeTruthy();

  try {
    await page.goto(`/projects/${project.id}/files`);
    await page.locator(".file-tree").getByRole("button", { name: path }).click();
    const toggle = page.getByRole("button", {
      name: "Toggle saved table preview",
    });
    await toggle.click();

    const editor = page.locator(".file-editor .monaco-editor");
    await expect(editor).toBeVisible();
    await editor.click();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.type("value\nunsaved draft\n");
    await expect(page.locator(".file-toolbar")).toContainText("●");

    await toggle.click();
    await expect(page.locator(".file-table-preview")).toContainText("original");
    await toggle.click();
    await expect(editor.locator(".view-lines")).toContainText("unsaved draft");
    await expect(page.locator(".file-toolbar")).toContainText("●");

    const persisted = await request.get(
      `/api/projects/${project.id}/file?path=${encodeURIComponent(path)}`,
    );
    expect((await persisted.json()).content).toBe(original);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
