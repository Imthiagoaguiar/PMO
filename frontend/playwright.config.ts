import { defineConfig, devices } from "@playwright/test"

// Testes no navegador. Rode pelo scripts/e2e.sh (ele sobe a API num banco descartável).
export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/.resultados",
  fullyParallel: false,
  workers: 1, // os testes compartilham o mesmo banco semeado
  retries: 0, // teste instável tem de aparecer, não ser escondido por nova tentativa
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "e2e/.relatorio" }]] : "list",
  use: {
    baseURL: "http://localhost:5180",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1360, height: 900 },
  },
  projects: [
    {
      name: "desktop",
      // PW_CHANNEL=chrome usa o Google Chrome instalado (sem baixar o Chromium do Playwright).
      use: { ...devices["Desktop Chrome"], viewport: { width: 1360, height: 900 }, channel: process.env.PW_CHANNEL || undefined },
    },
  ],
  webServer: {
    command: "npx vite --port 5180 --strictPort",
    url: "http://localhost:5180",
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
