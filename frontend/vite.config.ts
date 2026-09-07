import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    // The full integration file renders every data-rich route in jsdom. Give
    // slower Windows CI machines headroom without changing application timing.
    testTimeout: 10_000,
  },
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
  },
});
