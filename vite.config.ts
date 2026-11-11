import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    host: "0.0.0.0",
    allowedHosts: ["bscn1132585", "BSCN1132585"],
    port: 8080,
    strictPort: true,
    watch: {
      ignored: ["**/.worktrees/**", "**/.venv/**", "**/dist/**", "**/src/test/**"],
    },
    hmr: {
      overlay: false,
    },
    proxy: {
      "/api/full-picture": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
      "/api/testing": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
      "/api/metadata": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
      "/api/correlation": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
      "/api/auth": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
      "/api/agent/analytics/defects/export/download": {
        target: "http://127.0.0.1:3003",
        changeOrigin: true,
      },
      "/api": {
        target: "http://127.0.0.1:3004",
        changeOrigin: true,
      },
    },
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  optimizeDeps: {
    entries: ["index.html"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    dedupe: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime"],
  },
}));
