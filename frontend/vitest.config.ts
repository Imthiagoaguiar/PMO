import { mergeConfig } from "vite"
import { defineConfig } from "vitest/config"

import vite from "./vite.config"

// Testes unitários (Vitest): funções e componentes isolados. Os testes no navegador ficam em e2e/ (Playwright).
export default mergeConfig(
  vite,
  defineConfig({
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.{ts,tsx}"],
      restoreMocks: true,
    },
  }),
)
