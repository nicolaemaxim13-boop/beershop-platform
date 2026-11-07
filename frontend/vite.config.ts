import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/crm/",
  build: { outDir: "../web/crm-dist", emptyOutDir: true, sourcemap: false },
  server: {
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8018",
      "/assets": "http://127.0.0.1:8018",
      "/uploads": "http://127.0.0.1:8018",
      "/manage": "http://127.0.0.1:8018",
    },
  },
});
