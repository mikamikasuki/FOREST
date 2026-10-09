export function updateDefaultProviderDraft(text: string, providerId: string) {
  try {
    const current = JSON.parse(text);
    if (!current || typeof current !== "object" || Array.isArray(current))
      return text;
    return JSON.stringify(
      { ...current, default_provider_id: providerId },
      null,
      2,
    );
  } catch {
    return text;
  }
}
