import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
