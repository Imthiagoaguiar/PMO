import { expect, test } from "@playwright/test"

import { abrirProjeto, aviso, entrar, GERENTE, vigiarErros } from "./apoio"

test("gerente cria tarefa num pilar, avalia o pilar e baixa o relatório em PDF", async ({ page }) => {
  const erros = vigiarErros(page)
  await entrar(page, GERENTE)
  await abrirProjeto(page)

  // Nova tarefa na Iniciação, já no pilar TI.
  await page.getByRole("button", { name: "Nova tarefa em Iniciação" }).click()
  await page.getByLabel("Descrição").fill("Levantar requisitos")
  await page.getByRole("combobox", { name: "Pilar" }).click()
  await page.getByRole("option", { name: "TI", exact: true }).click()
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Tarefa criada.")).toBeVisible()
  await expect(page.getByRole("row", { name: /Levantar requisitos/ }).getByText("TI", { exact: true })).toBeVisible()

  // Farol do pilar.
  await page.getByRole("tab", { name: "Pilares" }).click()
  await page.getByRole("button", { name: "Editar pilar TI" }).click()
  await page.getByRole("combobox", { name: "Farol" }).click()
  await page.getByRole("option", { name: "Amarelo" }).click()
  await page.getByLabel("Situação").fill("Fornecedor da VPN atrasou uma semana.")
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Pilar atualizado.")).toBeVisible()
  await expect(page.getByText(/Atualizado em .* por Gil Gerente/)).toBeVisible()

  // Relatório PDF.
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Relatório PDF" }).click()])
  expect(download.suggestedFilename()).toBe("Relatório - Implantação ERP.pdf")
  expect(erros).toEqual([])
})
