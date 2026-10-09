import { describe, expect, it } from "vitest";
import { orderFileTreeEntries, visibleFileTreeEntries } from "./fileTree";

describe("file tree entries", () => {
  it("keeps each directory's descendants directly after its parent", () => {
    const files = [
      { path: "a/file.txt", is_dir: false },
      { path: "b/file.txt", is_dir: false },
      { path: "b", is_dir: true },
      { path: "a", is_dir: true },
    ];

    expect(orderFileTreeEntries(files).map((file) => file.path)).toEqual([
      "a",
      "a/file.txt",
      "b",
      "b/file.txt",
    ]);
  });

  it("filters a large tree with matching ancestors and collapsed directories", () => {
    const files = [
      { path: "records", is_dir: true },
      { path: "records/2026", is_dir: true },
      { path: "records/2026/notes.md", is_dir: false },
      { path: "other", is_dir: true },
      ...Array.from({ length: 10_000 }, (_, index) => ({
        path: `other/${index}.txt`,
        is_dir: false,
      })),
    ];
    const ordered = orderFileTreeEntries(files);

    expect(
      visibleFileTreeEntries(ordered, "notes.md", new Set()).map(
        (file) => file.path,
      ),
    ).toEqual(["records", "records/2026", "records/2026/notes.md"]);
    expect(
      visibleFileTreeEntries(
        ordered,
        "notes.md",
        new Set(["records/2026"]),
      ).map((file) => file.path),
    ).toEqual(["records", "records/2026"]);
  });
});
