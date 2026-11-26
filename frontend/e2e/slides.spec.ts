import path from "node:path"

import { expect, test } from "@playwright/test"

import { abrirProjeto, aviso, entrar, GERENTE, vigiarErros } from "./apoio"

const FOTOS = process.env.E2E_FOTOS

test("curva S, feedback e montador de slides com modelo da empresa", async ({ page }) => {
  const erros = vigiarErros(page)

  // O admin envia o modelo .pptx da empresa (fica disponível para todos).
  await entrar(page, "admin@e2e.com")
  await abrirProjeto(page)
  await page.getByRole("button", { name: "PowerPoint" }).click()
  await page.getByLabel("Arquivo do modelo da empresa").setInputFiles(path.join(import.meta.dirname, "arquivos", "modelo-empresa-4x3.pptx"))
  await expect(aviso(page, "disponível para todos")).toBeVisible()
  await page.keyboard.press("Escape")
  await page.getByRole("button", { name: /Ana Admin/ }).click()
  await page.getByRole("menuitem", { name: "Sair" }).click()

  // O gerente (dono) vê a curva S e o feedback e monta a apresentação no modelo da empresa.
  await entrar(page, GERENTE)
  await abrirProjeto(page)
  await page.getByRole("tab", { name: "Curva S", exact: true }).click()
  await expect(page.getByRole("tabpanel")).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/curva-s.png`, fullPage: true })
  await page.getByRole("tab", { name: "Feedback", exact: true }).click()
  await expect(page.getByText("Visível só para o dono do projeto e administradores.")).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/feedback.png`, fullPage: true })

  await page.getByRole("button", { name: "PowerPoint" }).click()
  const dialogo = page.getByRole("dialog")
  await dialogo.getByLabel("Incluir Marcos").uncheck()
  await dialogo.getByRole("button", { name: "Slide de texto" }).click()
  await dialogo.getByLabel("Título do slide").fill("Decisões da semana")
  await dialogo.getByLabel("Texto (um tópico por linha)").fill("Aprovar o escopo\nContratar o fornecedor")
  await dialogo.getByLabel("Notas do apresentador").fill("Falar em 2 minutos")
  await dialogo.getByRole("button", { name: "Subir Slide de texto" }).click()
  await expect(dialogo.getByRole("listitem").filter({ hasText: "Aprovar o escopo" })).toBeVisible() // prévia
  await dialogo.getByLabel("Modelo da apresentação").click()
  await page.getByRole("option", { name: "modelo-empresa-4x3" }).click()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/montador.png` })
  const download = page.waitForEvent("download")
  await dialogo.getByRole("button", { name: "Gerar PowerPoint" }).click()
  expect((await download).suggestedFilename()).toBe("Apresentação - Implantação ERP.pptx")
  await expect(aviso(page, "Apresentação baixada")).toBeVisible()
  expect(erros).toEqual([])
})
