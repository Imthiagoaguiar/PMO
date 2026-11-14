import { expect, type Page } from "@playwright/test"

export const SENHA = "Ponte-Azul-Gaveta-2919"
export const GERENTE = "gerente@e2e.com"
export const EDITORA = "editora@e2e.com"
export const LEITORA = "leitora@e2e.com"

export async function entrar(page: Page, email: string) {
  await page.goto("/login")
  await page.getByLabel("E-mail").fill(email)
  await page.getByLabel("Senha", { exact: true }).fill(SENHA)
  await page.getByRole("button", { name: "Entrar" }).click()
  await page.waitForURL(/\/projetos/)
}

export async function abrirProjeto(page: Page) {
  await page.goto("/projetos")
  await page.getByRole("link", { name: "Implantação ERP" }).first().click()
  await expect(page.getByRole("heading", { name: "Implantação ERP" })).toBeVisible()
}

export function aviso(page: Page, texto: string | RegExp) {
  return page.locator("[data-sonner-toast]").filter({ hasText: texto }).first()
}

/** Nenhum erro de JavaScript na página durante o teste. */
export function vigiarErros(page: Page): string[] {
  const erros: string[] = []
  page.on("pageerror", (e) => erros.push(e.message))
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) erros.push(m.text())
  })
  return erros
}
