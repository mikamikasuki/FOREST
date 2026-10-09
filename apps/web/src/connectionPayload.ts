export function hostEditorConfig(item: Record<string, unknown> | null) {
  return item && item.config && typeof item.config === "object"
    ? (item.config as Record<string, unknown>)
    : item
      ? Object.fromEntries(
          Object.entries(item).filter(
            ([key]) => !["id", "name", "kind", "created_at", "updated_at"].includes(key),
          ),
        )
      : {};
}

export function hostPayload(
  name: string,
  config: Record<string, unknown>,
  kind?: unknown,
) {
  return {
    name,
    kind:
      typeof kind === "string"
        ? kind
        : typeof config.kind === "string"
          ? config.kind
          : "local",
    config,
  };
}
