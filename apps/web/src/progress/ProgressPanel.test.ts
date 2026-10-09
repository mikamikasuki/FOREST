import { describe, expect, it } from "vitest";
import { narrativeDraftAfterSettingsLoad } from "./ProgressPanel";
import type { SettingsView } from "./types";

const saved = {
  enabled: true,
  automatic: false,
  provider_id: "provider-1",
  cap_usd: 5,
  max_requests: 10,
} as SettingsView["settings"];

describe("narrative settings draft hydration", () => {
  it("fills an empty draft when settings load while the panel is already open", () => {
    expect(narrativeDraftAfterSettingsLoad(saved, null, true)).toBe(saved);
  });

  it("preserves edits made after the saved settings have loaded", () => {
    const edited = { ...saved, enabled: false };
    expect(narrativeDraftAfterSettingsLoad(saved, edited, true)).toBe(edited);
  });

  it("refreshes the draft from saved settings when the panel is closed", () => {
    const edited = { ...saved, enabled: false };
    expect(narrativeDraftAfterSettingsLoad(saved, edited, false)).toBe(saved);
  });
});
