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

test("cleanup stays disabled for invalid saved retention until corrected", async ({
  page,
}) => {
  await page.route("**/api/settings", (route) =>
    route.fulfill({ json: { retention_days: -1 } }),
  );
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Budget & defaults" }).click();
  await page.getByText("Storage & history cleanup", { exact: true }).click();

  const cleanup = page.getByRole("button", { name: "Clean up" });
  await expect(cleanup).toBeDisabled();
  await page.getByLabel("Days to retain").fill("10");
  await expect(cleanup).toBeEnabled();
});

test("cleanup requires an explicit value when saved settings fail to load", async ({
  page,
}) => {
  await page.route("**/api/settings", (route) =>
    route.fulfill({ status: 503, body: "settings unavailable" }),
  );
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Budget & defaults" }).click();
  await page.getByText("Storage & history cleanup", { exact: true }).click();

  const cleanup = page.getByRole("button", { name: "Clean up" });
  await expect(cleanup).toBeDisabled();
  await page.getByLabel("Days to retain").fill("14");
  await expect(cleanup).toBeEnabled();
});
