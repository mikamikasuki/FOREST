import { expect, test } from "@playwright/test";

test("a failed literature search clears results from the previous query", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/projects", {
    data: {
      name: `Failed literature search ${Date.now()}`,
      goal: "Avoid saving results returned for a different query.",
      budget: { max_runs: 2, seconds: 60, allow_paid: false },
    },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const previousTitle = `Previous query result ${Date.now()}`;
  const failedQuery = `failed query ${Date.now()}`;

  await page.route("**/api/library/search", async (route) => {
    const query = route.request().postDataJSON().query;
    if (query === "first successful query") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          results: [
            {
              title: previousTitle,
              authors: ["A. Researcher"],
              year: 2025,
              doi: "10.1000/previous-query",
              url: "https://example.org/previous-query",
            },
          ],
        }),
      });
      return;
    }
    await route.fulfill({ status: 500, body: "search unavailable" });
  });

  try {
    await page.goto(`/projects/${project.id}/library`);
    const search = page.getByRole("textbox", { name: "Literature search" });
    await search.fill("first successful query");
    await page.getByRole("button", { name: "Search literature" }).click();
    await expect(page.getByRole("heading", { name: previousTitle })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save" })).toBeVisible();

    await search.fill(failedQuery);
    const failure = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/library/search" &&
        response.request().postDataJSON().query === failedQuery,
    );
    await page.getByRole("button", { name: "Search literature" }).click();
    expect((await failure).status()).toBe(500);

    await expect(page.getByRole("heading", { name: previousTitle })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
    await expect(page.getByText("No matching papers", { exact: true })).toBeVisible();
    const library = await (await request.get(`/api/library?project_id=${project.id}`)).json();
    expect(library).toEqual([]);
  } finally {
    await request.delete(`/api/projects/${project.id}`);
  }
});
