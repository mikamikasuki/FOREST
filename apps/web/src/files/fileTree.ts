export type FileTreeEntry = {
  path: string;
  is_dir?: boolean;
};

export function orderFileTreeEntries<T extends FileTreeEntry>(files: T[]): T[] {
  const children = new Map<string, T[]>();
  for (const file of files) {
    const separator = file.path.lastIndexOf("/");
    const parent = separator < 0 ? "" : file.path.slice(0, separator);
    const siblings = children.get(parent) ?? [];
    siblings.push(file);
    children.set(parent, siblings);
  }

  for (const siblings of children.values())
    siblings.sort(
      (left, right) =>
        Number(Boolean(right.is_dir)) - Number(Boolean(left.is_dir)) ||
        left.path.localeCompare(right.path),
    );

  const ordered: T[] = [];
  const pending = [...(children.get("") ?? [])].reverse();
  while (pending.length) {
    const file = pending.pop()!;
    ordered.push(file);
    if (!file.is_dir) continue;
    const descendants = children.get(file.path);
    if (descendants)
      for (let index = descendants.length - 1; index >= 0; index--)
        pending.push(descendants[index]);
  }
  return ordered;
}

export function visibleFileTreeEntries<T extends FileTreeEntry>(
  files: T[],
  filter: string,
  collapsedDirectories: Set<string>,
): T[] {
  const normalizedFilter = filter.toLowerCase();
  const matchingPaths = new Set<string>();
  const directoriesWithMatches = new Set<string>();

  for (const file of files) {
    if (!file.path.toLowerCase().includes(normalizedFilter)) continue;
    matchingPaths.add(file.path);
    let separator = file.path.lastIndexOf("/");
    while (separator >= 0) {
      const parent = file.path.slice(0, separator);
      directoriesWithMatches.add(parent);
      separator = file.path.lastIndexOf("/", separator - 1);
    }
  }

  return files.filter((file) => {
    if (
      !matchingPaths.has(file.path) &&
      !(file.is_dir && directoriesWithMatches.has(file.path))
    )
      return false;
    let separator = file.path.lastIndexOf("/");
    while (separator >= 0) {
      const parent = file.path.slice(0, separator);
      if (collapsedDirectories.has(parent)) return false;
      separator = file.path.lastIndexOf("/", separator - 1);
    }
    return true;
  });
}
