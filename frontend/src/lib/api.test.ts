import { describe, expect, it } from "vitest"

import { ApiError, criarGeradorDeChave, ehConflitoDeVersao } from "./api"

describe("criarGeradorDeChave (Idempotency-Key)", () => {
  it("mesmo conteúdo = mesma chave (duplo clique não duplica o projeto)", () => {
    const chaves = criarGeradorDeChave()
    expect(chaves.para('{"nome":"ERP"}')).toBe(chaves.para('{"nome":"ERP"}'))
  })
  it("conteúdo diferente = chave nova", () => {
    const chaves = criarGeradorDeChave()
    expect(chaves.para('{"nome":"ERP"}')).not.toBe(chaves.para('{"nome":"CRM"}'))
  })
  it("renovar gera chave nova até para o mesmo conteúdo (depois de criar com sucesso)", () => {
    const chaves = criarGeradorDeChave()
    const primeira = chaves.para("x")
    chaves.renovar()
    expect(chaves.para("x")).not.toBe(primeira)
  })
})

describe("ehConflitoDeVersao", () => {
  it("reconhece o 409 do bloqueio otimista", () => {
    const erro = new ApiError(409, "Este item foi alterado por outra pessoa enquanto você editava.")
    expect(ehConflitoDeVersao(erro)).toBe(true)
  })
  it("não confunde com outros 409 nem com outros erros", () => {
    expect(ehConflitoDeVersao(new ApiError(409, "Usuário já é membro do projeto."))).toBe(false)
    expect(ehConflitoDeVersao(new ApiError(422, "alterado por outra pessoa"))).toBe(false)
    expect(ehConflitoDeVersao(new Error("alterado por outra pessoa"))).toBe(false)
  })
})
