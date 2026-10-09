import { describe, expect, it } from "vitest";
import { createFileSelectionGuard } from "./fileSelection";

describe("file selection request guard", () => {
  it("ignores a late response after another file has been selected", () => {
    const selection = createFileSelectionGuard();
    const largeFileRequest = selection.select("large.txt");
    const smallFileRequest = selection.select("small.txt");

    expect(selection.isCurrent(largeFileRequest)).toBe(false);
    expect(selection.isCurrent(smallFileRequest)).toBe(true);
    expect(selection.current()).toEqual(smallFileRequest);
  });

  it("invalidates an earlier request when the same path is reopened", () => {
    const selection = createFileSelectionGuard();
    const firstRead = selection.select("notes.md");
    const secondRead = selection.select("notes.md");

    expect(selection.isCurrent(firstRead)).toBe(false);
    expect(selection.isCurrent(secondRead)).toBe(true);
  });
});
