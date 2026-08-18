import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/dev_dashboard/",
  plugins: [react()],
  server: {
    proxy: {
      "/dev_dashboard/api": "http://127.0.0.1:4310",
      "/api": "http://127.0.0.1:4310",
    },
  },
});
