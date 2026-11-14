import { afterEach, describe, expect, it, vi } from "vitest"

import * as fmt from "./formato"

describe("data", () => {
  it("formata sem passar por Date (o fuso não pode deslocar o dia)", () => {
    expect(fmt.data("2026-03-01")).toBe("01/03/2026")
    expect(fmt.data("2026-12-31")).toBe("31/12/2026")
  })
  it("mostra um traço quando não há data", () => {
    expect(fmt.data(null)).toBe("—")
    expect(fmt.data(undefined)).toBe("—")
  })
})

describe("moeda", () => {
  it("usa o formato brasileiro", () => {
    expect(fmt.moeda("1500000.5").replace(/\s/g, " ")).toBe("R$ 1.500.000,50")
    expect(fmt.moeda(0).replace(/\s/g, " ")).toBe("R$ 0,00")
  })
  it("valor vazio vira traço, não R$ 0,00", () => {
    expect(fmt.moeda(null)).toBe("—")
    expect(fmt.moeda("")).toBe("—")
  })
})

describe("pluralizar", () => {
  it("formata o número e escolhe a forma certa", () => {
    expect(fmt.pluralizar(1, "tarefa", "tarefas")).toBe("1 tarefa")
    expect(fmt.pluralizar(0, "tarefa", "tarefas")).toBe("0 tarefas")
    expect(fmt.pluralizar(20000, "tarefa", "tarefas")).toBe("20.000 tarefas")
  })
})

describe("atrasada", () => {
  afterEach(() => vi.useRealTimers())

  it("compara com o dia LOCAL de quem está usando", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 2, 15, 23, 30)) // 15/03 às 23h30 no fuso local
    expect(fmt.hojeISO()).toBe("2026-03-15")
    expect(fmt.atrasada("2026-03-14", 50)).toBe(true)
    expect(fmt.atrasada("2026-03-15", 50)).toBe(false) // vence hoje: ainda não está atrasada
    expect(fmt.atrasada("2026-03-14", 100)).toBe(false) // concluída nunca está atrasada
    expect(fmt.atrasada(null, 0)).toBe(false)
  })
})
