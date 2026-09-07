import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    restoreMocks: true,
    // App.test renders the complete data-rich shell and all eight routes.
    // Allow slower Windows hosts enough headroom without changing application
    // timers or polling behaviour.
    testTimeout: 10_000,
  },
});
