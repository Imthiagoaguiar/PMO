import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  // Use "localhost" (não 127.0.0.1) para o cookie do refresh token ser do mesmo site que a API.
  server: { host: "localhost", port: 5173, strictPort: true },
})
