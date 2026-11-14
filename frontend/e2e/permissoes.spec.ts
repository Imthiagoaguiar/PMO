import { expect, test } from "@playwright/test"

import { abrirProjeto, aviso, entrar, LEITORA, vigiarErros } from "./apoio"

test("visualizadora vê e comenta, mas não encontra nada para alterar", async ({ page }) => {
  const erros = vigiarErros(page)
  await entrar(page, LEITORA)
  await abrirProjeto(page)

  await expect(page.getByText("Termo de Abertura")).toBeVisible()
  await expect(page.getByRole("button", { name: "Editar tarefa" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Excluir tarefa" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: /Nova tarefa em/ })).toHaveCount(0)
  await page.getByRole("tab", { name: "Pilares" }).click()
  await expect(page.getByRole("button", { name: /Editar pilar/ })).toHaveCount(0)

  // Comentar é permitido.
  await page.getByRole("tab", { name: "Comentários" }).click()
  await page.getByRole("textbox", { name: "Novo comentário" }).fill("Visto pela Lia.")
  await page.getByRole("button", { name: "Comentar" }).click()
  await expect(page.getByText("Visto pela Lia.")).toBeVisible()
  await expect(aviso(page, /erro/i)).toHaveCount(0)
  expect(erros).toEqual([])
})
