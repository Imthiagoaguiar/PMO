import { expect, test } from "@playwright/test"

import { abrirProjeto, aviso, entrar, GERENTE, vigiarErros } from "./apoio"

const FOTOS = process.env.E2E_FOTOS

test("botões de andamento, anexos, gargalos, arquivos e apresentação em PowerPoint", async ({ page }) => {
  const erros = vigiarErros(page)
  await entrar(page, GERENTE)
  await abrirProjeto(page)

  // ---------- Botões de andamento (diálogo) ----------
  await page.getByRole("button", { name: "Nova tarefa em Execução" }).click()
  await page.getByLabel("Descrição").fill("Entregar relatório")
  await page.getByLabel("Início").fill("2026-01-05")
  await page.getByLabel("Término").fill("2026-01-20")
  await page.getByRole("group", { name: "Andamento" }).getByRole("button", { name: "50%" }).click()
  await expect(page.getByLabel("% concluído")).toHaveValue("50")
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Tarefa criada")).toBeVisible()

  // ---------- Atalho de andamento na linha ----------
  const linha = page.getByRole("row").filter({ hasText: "Entregar relatório" })
  await linha.getByRole("button", { name: /Andamento de Entregar relatório: 50%/ }).click()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/andamento-rapido.png` })
  await page.getByRole("group", { name: "Andamento" }).getByRole("button", { name: "100%" }).click()
  await expect(aviso(page, "“Entregar relatório”: 100%")).toBeVisible()

  // ---------- Anexos ----------
  await linha.getByRole("button", { name: "Editar tarefa" }).click()
  await page.getByLabel("Escolher arquivos para anexar").setInputFiles({
    name: "ata da reunião.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 ata"),
  })
  await expect(aviso(page, "“ata da reunião.pdf” anexado")).toBeVisible()
  await expect(page.getByRole("dialog").getByText("ata da reunião.pdf")).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/anexos.png` })
  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Baixar ata da reunião.pdf" }).click()
  expect((await download).suggestedFilename()).toBe("ata da reunião.pdf")
  await page.keyboard.press("Escape")

  // ---------- Arquivos (histórico do projeto) ----------
  await page.getByRole("tab", { name: "Arquivos" }).click()
  await expect(page.getByRole("cell", { name: "ata da reunião.pdf", exact: true })).toBeVisible()

  // ---------- Gargalos ----------
  await page.getByRole("tab", { name: "Gargalos" }).click()
  await expect(page.getByText("Onde o prazo escapou")).toBeVisible()
  await expect(page.getByText("Dias de atraso acumulados", { exact: true })).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/gargalos.png`, fullPage: true })

  // ---------- PowerPoint ----------
  const pptx = page.waitForEvent("download")
  await page.getByRole("button", { name: "PowerPoint" }).click()
  expect((await pptx).suggestedFilename()).toMatch(/^Apresentação - Implantação ERP\.pptx$/)
  await expect(aviso(page, "Apresentação baixada")).toBeVisible()

  expect(erros).toEqual([])
})
