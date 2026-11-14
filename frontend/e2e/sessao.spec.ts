import { expect, test } from "@playwright/test"

import { entrar, GERENTE } from "./apoio"

test("duas abas renovando a sessão ao mesmo tempo continuam logadas", async ({ context }) => {
  const a = await context.newPage()
  await entrar(a, GERENTE)
  const b = await context.newPage()
  await b.goto("/projetos")
  await expect(b.getByRole("heading", { name: "Projetos" })).toBeVisible()

  for (let rodada = 0; rodada < 3; rodada++) {
    await Promise.all([a.reload(), b.reload()]) // as duas renovam a sessão juntas
    await Promise.all([a, b].map((p) => expect(p.getByRole("heading", { name: "Projetos" })).toBeVisible()))
  }
  const c = await context.newPage()
  await c.goto("/minhas-tarefas")
  await expect(c.getByRole("heading", { name: "Minhas tarefas" })).toBeVisible() // e a sessão segue válida
})
