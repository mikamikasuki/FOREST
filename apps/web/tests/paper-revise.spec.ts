import { expect, test } from "@playwright/test";

test("paper revision ignores repeated clicks while the request is pending", async ({
  page,
  request,
}) => {
  const project = await (
    await request.post("/api/projects", {
      data: {
        name: `Paper revision dedup ${Date.now()}`,
        goal: "Avoid duplicate manuscript revisions",
      },
    })
  ).json();
  let attempts = 0;
  await page.route(`**/api/papers/${project.id}/revise`, async (route) => {
    attempts += 1;
    await new Promise((resolve) => setTimeout(resolve, 400));
    if (attempts === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Temporary revision failure" }),
      });
      return;
    }
    await route.fulfill({ status: 202, json: { id: "queued-revision" } });
  });

  try {
    await page.goto(`/projects/${project.id}/paper`);
    const instruction = page.getByPlaceholder("Describe the revision…");
    await instruction.fill("Clarify the results");
    const revise = page.getByRole("button", { name: "Revise" });
    await expect(revise).toBeEnabled();

    await revise.click();
    await expect(revise).toBeDisabled();
    await revise.dispatchEvent("click");
    expect(attempts).toBe(1);
    await expect(page.getByText("Temporary revision failure")).toBeVisible();
    await expect(revise).toBeEnabled();
    expect(attempts).toBe(1);

    await revise.click();
    await expect(revise).toBeDisabled();
    await expect(page.getByText("Paper revision queued")).toBeVisible();
    expect(attempts).toBe(2);
    await expect(instruction).toHaveValue("");
  } finally {
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});
