import { expect, test, type Page } from "@playwright/test"

import { abrirProjeto, EDITORA, entrar, GERENTE, LEITORA } from "./apoio"

/**
 * Varredura: cada tipo de usuário abre todas as páginas, abas e diálogos que pode ver. Falha com
 * qualquer erro de JavaScript, erro no console ou resposta 5xx da API (4xx inesperados vão listados).
 */
const ADMIN = "admin@e2e.com"
const ABAS = ["Cronograma", "Pilares", "Orçamento", "Linha do tempo", "Gargalos", "Comentários", "Arquivos", "Membros", "Histórico"]

function vigiar(page: Page) {
  const problemas: string[] = []
  page.on("pageerror", (e) => problemas.push(`JS: ${e.message}`))
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) problemas.push(`console: ${m.text()}`)
  })
  page.on("response", (r) => {
    const caminho = new URL(r.url()).pathname
    // Esperado: ao abrir o site sem sessão, o front pergunta "ainda tenho sessão?" e a resposta é 401.
    if (caminho === "/auth/refresh" && r.status() === 401) return
    if (r.url().includes(":8100") && r.status() >= 400) problemas.push(`${r.status()} ${r.request().method()} ${caminho}`)
  })
  return problemas
}

async function abrirEFechar(page: Page, botao: RegExp | string) {
  const b = page.getByRole("button", { name: botao }).first()
  if (!(await b.isVisible())) return
  await b.click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("dialog")).toBeHidden()
}

async function percorrerProjeto(page: Page, podeEditar: boolean) {
  await abrirProjeto(page)
  for (const aba of ABAS) {
    await page.getByRole("tab", { name: aba, exact: true }).click()
    await expect(page.getByRole("tabpanel")).toBeVisible()
    await page.waitForLoadState("networkidle")
  }
  if (!podeEditar) return
  await page.getByRole("tab", { name: "Cronograma", exact: true }).click()
  await abrirEFechar(page, "Nova tarefa em Iniciação")
  await abrirEFechar(page, "Fases")
  await page.getByRole("button", { name: "Editar tarefa" }).first().click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.waitForLoadState("networkidle")
  await page.keyboard.press("Escape")
  await page.getByRole("tab", { name: "Linha do tempo", exact: true }).click()
  await abrirEFechar(page, "Adicionar marco")
  await page.getByRole("tab", { name: "Orçamento", exact: true }).click()
  await abrirEFechar(page, /Adicionar item|^Item$/)
  await page.getByRole("tab", { name: "Pilares", exact: true }).click()
  await page.waitForLoadState("networkidle")
}

for (const [quem, email, editar] of [["gerente", GERENTE, true], ["editora", EDITORA, true], ["leitora", LEITORA, false], ["admin", ADMIN, true]] as const) {
  test(`varredura: ${quem}`, async ({ page }) => {
    const problemas = vigiar(page)
    await entrar(page, email)
    await percorrerProjeto(page, editar)
    await page.goto("/minhas-tarefas")
    await expect(page.getByRole("heading", { name: "Minhas tarefas" })).toBeVisible()
    if (quem === "admin") {
      for (const [rota, titulo] of [["/usuarios", "Usuários"], ["/auditoria", "Auditoria"], ["/vigia", "Vigia"]]) {
        await page.goto(rota)
        await expect(page.getByRole("heading", { name: titulo }).first()).toBeVisible()
        await page.waitForLoadState("networkidle")
      }
    }
    // Menu do usuário (silenciar lembretes, 2FA, senha) abre sem erro.
    await page.getByRole("button", { name: new RegExp(email.slice(0, 2), "i") }).last().click().catch(() => {})
    await page.keyboard.press("Escape")
    expect(problemas, problemas.join("\n")).toEqual([])
  })
}
