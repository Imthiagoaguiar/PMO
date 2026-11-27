import { expect, test } from "@playwright/test"

import { abrirProjeto, aviso, entrar, GERENTE, vigiarErros } from "./apoio"

const FOTOS = process.env.E2E_FOTOS // pasta para as capturas de tela (opcional)

test("fases editáveis, dependência que bloqueia, marco na linha do tempo e orçamento com gráfico", async ({ page }) => {
  const erros = vigiarErros(page)
  await entrar(page, GERENTE)
  await abrirProjeto(page)

  // ---------- Fases: renomear e criar ----------
  await page.getByRole("button", { name: "Fases", exact: true }).click()
  const nome = page.getByRole("textbox", { name: "Nome da fase 4" })
  await nome.fill("Garantia")
  await nome.press("Enter")
  await expect(aviso(page, "Fase renomeada")).toBeVisible()
  await page.getByRole("textbox", { name: "Nome da nova fase" }).fill("Homologação")
  await page.getByRole("button", { name: "Adicionar" }).click()
  await expect(aviso(page, "Fase “Homologação” criada")).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/fases.png` })
  await page.keyboard.press("Escape")
  await expect(page.getByText("4. Garantia")).toBeVisible()
  await expect(page.getByText("6. Homologação")).toBeVisible()

  // ---------- Dependência: B só começa depois de A ----------
  await page.getByRole("button", { name: "Nova tarefa em Homologação" }).click()
  await page.getByLabel("Descrição").fill("Homologar com o cliente")
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Tarefa criada")).toBeVisible()

  const linha = page.getByRole("row").filter({ hasText: "Homologar com o cliente" })
  await linha.getByRole("button", { name: "Editar tarefa" }).click()
  await page.getByRole("combobox", { name: "Tarefa da qual esta depende" }).click()
  await page.getByRole("option", { name: /Termo de Abertura/ }).click()
  await page.getByRole("button", { name: "Adicionar" }).click()
  await expect(aviso(page, "Dependência criada")).toBeVisible()
  await expect(page.getByRole("dialog").getByText("Só começa depois de “Termo de Abertura”")).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/dependencia.png` })
  await page.getByLabel("% concluído").fill("30")
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "só pode começar depois que “Termo de Abertura” terminar")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(linha.getByText("Aguardando")).toBeVisible()

  // ---------- Marco na linha do tempo ----------
  await page.getByRole("tab", { name: "Linha do tempo" }).click()
  await page.getByRole("button", { name: "Adicionar marco" }).click()
  await page.getByLabel("Título").fill("Pré-entrega")
  await page.getByRole("button", { name: "estrela" }).click()
  await page.getByRole("button", { name: "vermelho" }).click()
  await page.getByLabel("Data", { exact: true }).fill("2026-12-15")
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Marco adicionado")).toBeVisible()
  await expect(page.getByRole("button", { name: /Pré-entrega: estrela em 15\/12\/2026/ })).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/linha-do-tempo.png`, fullPage: true })

  // ---------- Orçamento ----------
  await page.getByRole("tab", { name: "Orçamento" }).click()
  await page.getByRole("button", { name: "Adicionar item" }).click()
  await page.getByLabel("Descrição").fill("Licenças do ERP")
  await page.getByLabel("Valor orçado (R$)").fill("40000")
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Item adicionado")).toBeVisible()
  for (const [valor, data] of [["15000", "2026-10-05"], ["30000", "2026-11-10"]]) {
    await page.getByRole("button", { name: "Lançar gasto em Licenças do ERP" }).click()
    await page.getByLabel("Descrição").fill(`NF ${data}`)
    await page.getByLabel("Data", { exact: true }).fill(data)
    await page.getByLabel("Valor (R$)").fill(valor)
    if (valor === "30000") {
      // 45 mil de 40 mil orçados: estoura o item, e a justificativa vira obrigatória.
      await expect(page.getByRole("button", { name: "Salvar" })).toBeDisabled()
      await page.getByLabel("Por que está estourando? (obrigatório)").fill("Licenças extras para a filial")
    }
    await page.getByRole("button", { name: "Salvar" }).click()
    await expect(aviso(page, "Gasto lançado").first()).toBeVisible()
  }
  await expect(page.getByText("já passou do orçado")).toBeVisible()
  await expect(page.getByText("Por quê: Licenças extras para a filial")).toBeVisible()
  await expect(page.getByRole("img", { name: /acumulados por mês/ })).toBeVisible()
  await expect(page.getByText("Estourou", { exact: true })).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/orcamento.png`, fullPage: true })

  expect(erros).toEqual([])
})
