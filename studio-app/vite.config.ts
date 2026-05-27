import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

function marketingBaseSlashRedirect() {
  return {
    name: "marketing-base-slash-redirect",
    enforce: "pre" as const,
    configureServer(server: import("vite").ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const url = "url" in req && typeof req.url === "string" ? req.url : "";
        if (url === "/marketing" || url.startsWith("/marketing?")) {
          const query = url.slice("/marketing".length);
          res.statusCode = 302;
          res.setHeader("Location", `/marketing/${query}`);
          res.end();
          return;
        }
        next();
      });
    },
  };
}

export default defineConfig({
  base: "/marketing/",
  plugins: [marketingBaseSlashRedirect(), tailwindcss(), react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8001",
      "/video-mvp": "http://127.0.0.1:8001",
      "/download": "http://127.0.0.1:8001",
      "/outputs": "http://127.0.0.1:8001",
      "/static": "http://127.0.0.1:8001",
      "/room-types": "http://127.0.0.1:8001",
      "/styles": "http://127.0.0.1:8001",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
