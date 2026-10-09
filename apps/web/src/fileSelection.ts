export type FileSelection = { path: string; generation: number };

export function createFileSelectionGuard() {
  let active: FileSelection = { path: "", generation: 0 };
  return {
    select(path: string): FileSelection {
      active = { path, generation: active.generation + 1 };
      return active;
    },
    current() {
      return active;
    },
    isCurrent(selection: FileSelection) {
      return (
        selection.path === active.path &&
        selection.generation === active.generation
      );
    },
  };
}
