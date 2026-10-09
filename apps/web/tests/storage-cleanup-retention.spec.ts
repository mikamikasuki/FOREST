import { expect, test } from "@playwright/test";

test("storage cleanup uses the saved retention period", async ({ page }) => {
  await page.route("**/api/settings", (route) =>
    route.fulfill({ json: { retention_days: 7 } }),
  );
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Budget & defaults" }).click();
  await page.getByText("Storage & history cleanup", { exact: true }).click();

  await expect(page.getByLabel("Days to retain")).toHaveValue("7");
});
