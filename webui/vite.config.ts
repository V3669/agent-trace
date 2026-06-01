import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // In dev, proxy API calls to the Python server running on 8789.
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8789",
        changeOrigin: true,
      },
    },
  },
  build: {
    // Output into agenttrace/webui/dist so the Python server can serve it.
    outDir: "../agenttrace/webui/dist",
    emptyOutDir: true,
  },
});
