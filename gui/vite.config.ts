import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      // Forward API calls to the FastAPI backend during dev.
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": "/src",
    },
  },
});
