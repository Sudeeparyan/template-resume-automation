import { defineConfig } from "vite";
import type { ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard server answers both the shell API (/api/...) and each profile's
// own API (/p/<id>/api/...); the page itself is served by Vite at /p/<id>/.
const backend: ProxyOptions = {
  target: "http://127.0.0.1:8000",
  changeOrigin: true,
  configure(proxy) {
    proxy.on("proxyReq", (outgoing, incoming) => {
      if (
        ["http://127.0.0.1:5173", "http://localhost:5173"].includes(
          incoming.headers.origin || "",
        )
      )
        outgoing.setHeader("Origin", "http://127.0.0.1:8000");
    });
  },
};

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist" },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": backend,
      "^/p/[a-z0-9-]+/api/.*": backend,
    },
  },
});
