export function hostPayload(name: string, config: Record<string, unknown>) {
  return {
    name,
    kind: typeof config.kind === "string" ? config.kind : "local",
    config,
  };
}
