import { expect, test } from "@playwright/test";

test("projects search matches a description when the goal is empty", async ({
  page,
  request,
}) => {
  const marker = `description-search-${Date.now()}`;
  const created = await request.post("/api/projects", {
    data: {
      name: `Searchable project ${Date.now()}`,
      goal: "",
      description: `Unique archive note: ${marker}`,
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();

  try {
    await page.goto("/");
    await page.getByLabel("Search projects").fill(marker);

    const card = page.locator(".project-card");
    await expect(card).toHaveCount(1);
    await expect(card).toContainText(`Unique archive note: ${marker}`);
  } finally {
    const deleted = await request.delete(`/api/projects/${project.id}`);
    expect(deleted.ok()).toBeTruthy();
  }
});
