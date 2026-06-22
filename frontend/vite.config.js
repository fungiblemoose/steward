import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev server proxies API + WebSocket to the FastAPI backend on :8080.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: true },
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
