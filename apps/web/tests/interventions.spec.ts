import { expect, test } from "@playwright/test";

test("two live tabs preserve reviewed subtree scope and a dirty node draft", async ({
  page,
  context,
  request,
}) => {
  test.setTimeout(90000);
  const project = await (
    await request.post("/api/projects", {
      data: {
        name: `Concurrent intervention ${Date.now()}`,
        mode: "manual",
        goal: "Review exact graph impact before adoption",
      },
    })
  ).json();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const second = await context.newPage();
  second.on("pageerror", (e) => errors.push(e.message));
  try {
    let graph = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const initial = await request.post(
      `/api/projects/${project.id}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: graph.revision,
          operation: "add_node",
          params: { type: "goal", title: "Reviewed root" },
        },
      },
    );
    expect(initial.ok()).toBeTruthy();
    const root = (await initial.json()).graph.nodes[0];
    await page.goto(`/projects/${project.id}/workspace`);
    await second.goto(`/projects/${project.id}/workspace`);
    await page
      .getByRole("button", { name: "Path actions", exact: true })
      .click();
    const operation = page.getByRole("dialog", {
      name: "Research path actions",
    });
    await operation
      .getByRole("combobox", { name: "Operation", exact: true })
      .selectOption("clone_subtree");
    await operation
      .getByRole("listbox", { name: "Target nodes (multiple selection)" })
      .selectOption(root.id);
    await operation.getByRole("textbox", { name: /^Parameters/ }).fill("{}");
    await operation
      .getByRole("button", { name: "Preview impact", exact: true })
      .click();
    const impact = page.getByRole("dialog", { name: "Change impact" });
    await expect(impact).toBeVisible();
    await second
      .locator(".workspace-toolbar")
      .getByRole("button", { name: "Add node", exact: true })
      .click();
    const add = second.getByRole("dialog");
    await add.getByLabel("Node title").fill("Added after review");
    await add.getByRole("button", { name: "Create node", exact: true }).click();
    await expect(
      second
        .locator(".research-node h3")
        .filter({ hasText: "Added after review" }),
    ).toHaveText("Added after review");
    graph = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const newChild = graph.nodes.find(
      (n: { title: string }) => n.title === "Added after review",
    );
    const dependency = await request.post(
      `/api/projects/${project.id}/graph/commands`,
      {
        data: {
          request_id: crypto.randomUUID(),
          expected_revision: graph.revision,
          operation: "add_dependency",
          params: { source: root.id, target: newChild.id, kind: "execution" },
        },
      },
    );
    expect(dependency.ok()).toBeTruthy();
    await expect(page.locator(".research-node")).toHaveCount(2);
    const beforeApply = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    const staleResponse = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().endsWith(`/api/projects/${project.id}/graph/commands`),
    );
    await impact
      .getByRole("button", { name: "Apply changes", exact: true })
      .click();
    expect((await staleResponse).status()).toBe(409);
    await expect(impact).toBeVisible();
    const afterApply = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    expect(afterApply.revision).toBe(beforeApply.revision);
    expect(afterApply.nodes).toEqual(beforeApply.nodes);
    await impact
      .getByRole("button", { name: "Back to editing", exact: true })
      .click();
    await operation
      .getByRole("button", { name: "Cancel", exact: true })
      .click();
    await page.locator(`.react-flow__node[data-id="${root.id}"]`).click();
    await second.locator(`.react-flow__node[data-id="${root.id}"]`).click();
    await page.getByLabel("Node title").fill("Unsaved local question");
    await second.getByLabel("Node title").fill("Saved second-tab question");
    await second
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    await expect(
      page.locator(`.react-flow__node[data-id="${root.id}"] h3`),
    ).toHaveText("Saved second-tab question");
    await expect(page.getByLabel("Node title")).toHaveValue(
      "Unsaved local question",
    );
    await page
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "Node changed elsewhere" }),
    ).toBeVisible();
    const final = await (
      await request.get(`/api/projects/${project.id}/graph`)
    ).json();
    expect(
      final.nodes.find((n: { id: string }) => n.id === root.id).title,
    ).toBe("Saved second-tab question");
    expect(errors).toEqual([]);
  } finally {
    await second.close().catch(() => {});
    await request.delete(`/api/projects/${project.id}`).catch(() => {});
  }
});
