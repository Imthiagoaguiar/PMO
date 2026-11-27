import { expect, test } from "@playwright/test"

import { aviso, entrar, GERENTE, vigiarErros } from "./apoio"

const FOTOS = process.env.E2E_FOTOS

test("orçamento mês a mês, gasto que estoura com justificativa, projetado, ajuste do gasto e troca de projeto", async ({ page }) => {
  const erros = vigiarErros(page)
  await entrar(page, GERENTE)
  await page.getByRole("link", { name: "Expansão da Filial" }).first().click()
  await expect(page.getByRole("heading", { name: "Expansão da Filial" })).toBeVisible()
  await page.getByRole("tab", { name: "Orçamento", exact: true }).click()
  await expect(page).toHaveURL(/aba=orcamento/)

  // Item orçado mês a mês: um valor em março e outro em abril; o total é a soma.
  await page.getByRole("button", { name: "Adicionar item" }).click()
  const item = page.getByRole("dialog")
  await item.getByLabel("Descrição").fill("Servidores")
  await item.getByRole("combobox", { name: "Categoria" }).click()
  await page.getByRole("option", { name: "CAPEX (investimento)" }).click()
  await item.getByRole("radio", { name: "Mês a mês" }).click()
  await item.getByLabel("Mês 1", { exact: true }).fill("2026-03")
  await item.getByLabel("Valor do mês 1 (R$)").fill("6000")
  await item.getByRole("button", { name: "Mês", exact: true }).click()
  await item.getByLabel("Valor do mês 2 (R$)").fill("4000")
  await expect(item.getByText("R$ 10.000,00")).toBeVisible()
  await item.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Item adicionado")).toBeVisible()
  await expect(page.getByText("2 meses", { exact: true })).toBeVisible()

  // Gasto que estoura o item: o botão só libera com a justificativa.
  await page.getByRole("button", { name: "Lançar gasto em Servidores" }).click()
  const gasto = page.getByRole("dialog")
  await gasto.getByLabel("Descrição").fill("NF 1020")
  await gasto.getByLabel("Valor (R$)").fill("11000")
  await expect(gasto.getByRole("button", { name: "Salvar" })).toBeDisabled()
  await gasto.getByLabel("Por que está estourando? (obrigatório)").fill("Câmbio subiu 10%")
  await gasto.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Gasto lançado")).toBeVisible()
  await expect(page.getByText("Por quê: Câmbio subiu 10%")).toBeVisible()

  // Gasto futuro (projetado): fora do realizado, dentro da previsão final.
  await page.getByRole("button", { name: "Projetar gasto futuro em Servidores" }).click()
  const futuro = page.getByRole("dialog")
  await futuro.getByLabel("Descrição").fill("Parcela de manutenção")
  await futuro.getByLabel("Previsto para").fill("2027-02-10")
  await futuro.getByLabel("Valor (R$)").fill("2500")
  await futuro.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Gasto projetado")).toBeVisible()
  const capex = page.getByRole("row", { name: /CAPEX \(investimento\)/ })
  await expect(capex).toContainText("R$ 11.000,00")
  await expect(capex).toContainText("R$ 2.500,00")
  await expect(capex).toContainText("R$ 13.500,00")
  await page.getByRole("tab", { name: "Projetados (futuros)" }).click()
  await expect(page.getByRole("cell", { name: "Parcela de manutenção", exact: true })).toBeVisible()
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/orcamento-novo.png`, fullPage: true })

  // O projetado aconteceu: confirmar com o valor real (estoura de novo: pede a justificativa).
  await page.getByRole("button", { name: "Confirmar que “Parcela de manutenção” aconteceu" }).click()
  const confirmar = page.getByRole("dialog")
  await confirmar.getByLabel("Valor real (R$)").fill("2600")
  await confirmar.getByLabel("Por que está estourando? (obrigatório)").fill("Manutenção antecipada")
  await confirmar.getByRole("button", { name: "Confirmar gasto" }).click()
  await expect(aviso(page, "Gasto confirmado")).toBeVisible()
  await expect(page.getByText("Nenhum gasto projetado ainda.")).toBeVisible()

  // Corrigir o valor gasto pelo lápis do item: o sistema lança o ajuste com a diferença.
  await page.getByRole("button", { name: "Editar Servidores" }).click()
  const editar = page.getByRole("dialog")
  await expect(editar.getByLabel("Valor gasto até agora (R$)")).toHaveValue("13600")
  await editar.getByLabel("Valor gasto até agora (R$)").fill("12000")
  await expect(editar.getByText("Vamos lançar um ajuste de -R$ 1.600,00")).toBeVisible()
  await editar.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "valor gasto ajustado")).toBeVisible()
  await page.getByRole("tab", { name: "Realizados" }).click()
  await expect(page.getByRole("cell", { name: "Ajuste do valor gasto: de R$ 13.600,00 para R$ 12.000,00", exact: true })).toBeVisible()

  // Linha do tempo com as semanas (um marco basta para ela aparecer).
  await page.getByRole("tab", { name: "Linha do tempo", exact: true }).click()
  await page.getByRole("button", { name: "Adicionar marco" }).click()
  await page.getByLabel("Título").fill("Go-live da filial")
  await page.getByLabel("Data", { exact: true }).fill("2026-12-14")
  await page.getByRole("button", { name: "Salvar" }).click()
  await expect(aviso(page, "Marco adicionado")).toBeVisible()
  await expect(page.getByText("Semanas (segundas-feiras)")).toBeVisible()
  await expect(page.getByLabel("Semanas")).toContainText("14/12")
  if (FOTOS) await page.screenshot({ path: `${FOTOS}/linha-semanas.png`, fullPage: true })

  // Trocar de projeto pelo painel lateral: abre o outro projeto na mesma aba.
  await page.getByRole("tab", { name: "Orçamento", exact: true }).click()
  await page.getByRole("button", { name: "Trocar de projeto" }).click()
  const painel = page.getByRole("dialog")
  await expect(painel.getByRole("link", { name: /Expansão da Filial/ })).toHaveAttribute("aria-current", "page")
  if (FOTOS) {
    await page.waitForTimeout(500) // fim da animação do painel
    await page.screenshot({ path: `${FOTOS}/trocar-projeto.png` })
  }
  await painel.getByLabel("Buscar projeto").fill("implanta")
  await expect(painel.getByRole("link", { name: /Expansão da Filial/ })).toHaveCount(0)
  await painel.getByRole("link", { name: /Implantação ERP/ }).click()
  await expect(page.getByRole("heading", { name: "Implantação ERP" })).toBeVisible()
  await expect(page).toHaveURL(/aba=orcamento/)
  await expect(page.getByRole("tab", { name: "Orçamento", exact: true })).toHaveAttribute("aria-selected", "true")
  expect(erros).toEqual([])
})
