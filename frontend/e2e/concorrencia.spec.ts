import { expect, test } from "@playwright/test"

import { abrirProjeto, aviso, EDITORA, entrar, GERENTE } from "./apoio"

test("duas pessoas editando a mesma tarefa: a segunda é avisada e nada é sobrescrito", async ({ browser }) => {
  const gil = await (await browser.newContext()).newPage()
  const eva = await (await browser.newContext()).newPage()
  for (const [pagina, email] of [[gil, GERENTE], [eva, EDITORA]] as const) {
    await entrar(pagina, email)
    await abrirProjeto(pagina)
    await pagina.getByRole("row", { name: /Tarefa de concorrência/ }).getByRole("button", { name: "Editar tarefa" }).click()
    await expect(pagina.getByLabel("% concluído")).toBeVisible()
  }

  await gil.getByLabel("% concluído").fill("30")
  await gil.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(gil, "Tarefa atualizada.")).toBeVisible()

  await eva.getByLabel("Descrição").fill("Tarefa de concorrência (revisada)")
  await eva.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(eva, "alterado por outra pessoa")).toBeVisible()

  // A tela da Eva recarregou com os 30% do Gil; ela salva de novo sobre a versão atual.
  await eva.getByRole("row", { name: /Tarefa de concorrência/ }).getByRole("button", { name: "Editar tarefa" }).click()
  await expect(eva.getByLabel("% concluído")).toHaveValue("30")
})
