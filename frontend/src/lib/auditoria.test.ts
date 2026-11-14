import { describe, expect, it } from "vitest"

import { descreverAcao, descreverDetalhes } from "./auditoria"
import type { EventoAuditoria } from "./tipos"

const evento = (detalhes: Record<string, unknown> | null): EventoAuditoria =>
  ({ id: 1, criado_em: "2026-03-01T12:00:00Z", usuario_id: 1, acao: "tarefa.atualizada", entidade: "tarefa",
     entidade_id: 1, projeto_id: 1, detalhes, ip: null, usuario: null }) as EventoAuditoria

describe("descreverAcao", () => {
  it("traduz as ações conhecidas e mantém as desconhecidas", () => {
    expect(descreverAcao("pilar.atualizado")).toBe("atualizou um pilar")
    expect(descreverAcao("projeto.relatorio_pdf")).toBe("gerou o relatório em PDF")
    expect(descreverAcao("acao.nova")).toBe("acao.nova")
  })
})

describe("descreverDetalhes", () => {
  it("mostra mudanças como antes → depois, com datas no formato brasileiro", () => {
    const linhas = descreverDetalhes(evento({ percentual_conclusao: [0, 30], data_fim: [null, "2026-03-20"] }))
    expect(linhas).toContain("% concluído: 0 → 30")
    expect(linhas).toContain("término: vazio → 20/03/2026")
  })
  it("sem detalhes, nada", () => {
    expect(descreverDetalhes(evento(null))).toEqual([])
  })
})
