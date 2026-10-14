import { defineConfig } from "vitest/config";
import path from "node:path";

// Build-time only test runner (jsdom). Never shipped in the portal bundle, so INV-PORTAL-ZERO-NODE-DEP holds.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts"],
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
});
