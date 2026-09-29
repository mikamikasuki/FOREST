import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.FOREST_WEB_URL || "http://127.0.0.1:5173",
    channel: process.env.FOREST_BROWSER_CHANNEL || "chrome",
    headless: true,
    viewport: { width: 1440, height: 1000 },
  },
  outputDir: "test-results",
});
