import { readFileSync } from "node:fs"

import { expect, test } from "@playwright/test"

import { SENHA, vigiarErros } from "./apoio"

const EMAIL = "esqueceu@e2e.com"
const NOVA = "Lanterna-Verde-Cadeira-4471"

/** Sem Resend, em dev, a API escreve o link no log (scripts/e2e.sh passa o caminho). */
function linkDoLog(): string {
  const log = readFileSync(process.env.E2E_API_LOG ?? "", "utf8")
  const links = [...log.matchAll(/https?:\/\/[^\s"]+\/redefinir-senha#token=[\w.-]+/g)]
  expect(links.length, "link de redefinição no log da API").toBeGreaterThan(0)
  return links[links.length - 1][0]
}

test("esqueci a senha: pede o link, cria a nova senha e o link não vale de novo", async ({ page }) => {
  test.skip(!process.env.E2E_API_LOG, "rode pelo scripts/e2e.sh")
  const erros = vigiarErros(page)

  await page.goto("/login")
  await page.getByLabel("E-mail").fill(EMAIL)
  await page.getByRole("link", { name: "Esqueci minha senha" }).click()
  await expect(page.getByLabel("E-mail")).toHaveValue(EMAIL) // o e-mail digitado vem junto
  await page.getByRole("button", { name: "Enviar link" }).click()
  await expect(page.getByText("Confira seu e-mail", { exact: true })).toBeVisible()

  const link = linkDoLog()
  const caminho = new URL(link)
  await page.goto(caminho.pathname + caminho.hash)
  await expect(page).toHaveURL(/\/redefinir-senha$/) // o token sai da barra de endereço
  await page.getByLabel("Nova senha", { exact: true }).fill(NOVA)
  await page.getByLabel("Confirme a nova senha").fill(NOVA)
  await page.getByRole("button", { name: "Salvar nova senha" }).click()
  await page.waitForURL(/\/login/)

  await page.getByLabel("E-mail").fill(EMAIL)
  await page.getByLabel("Senha", { exact: true }).fill(SENHA)
  await page.getByRole("button", { name: "Entrar" }).click()
  await expect(page.getByRole("alert")).toContainText("E-mail ou senha inválidos") // a antiga não vale mais

  await page.getByLabel("Senha", { exact: true }).fill(NOVA)
  await page.getByRole("button", { name: "Entrar" }).click()
  await page.waitForURL(/\/projetos/)

  await page.goto(caminho.pathname + caminho.hash) // o mesmo link, de novo
  await page.getByLabel("Nova senha", { exact: true }).fill("Outra-Senha-Comprida-8830")
  await page.getByLabel("Confirme a nova senha").fill("Outra-Senha-Comprida-8830")
  await page.getByRole("button", { name: "Salvar nova senha" }).click()
  await expect(page.getByText("Link inválido ou expirado", { exact: true })).toBeVisible()
  expect(erros).toEqual([])
})
