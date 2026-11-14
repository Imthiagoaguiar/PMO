import { toast } from "sonner"

import { ApiError } from "@/lib/api"

/** "2026-04-17" -> "17/04/2026", sem passar por Date (evita o fuso deslocar o dia). */
export function data(iso: string | null | undefined): string {
  if (!iso) return "—"
  const [ano, mes, dia] = iso.slice(0, 10).split("-")
  return `${dia}/${mes}/${ano}`
}

export function dataHora(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })
}

const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" })

export function moeda(valor: string | number | null | undefined): string {
  if (valor === null || valor === undefined || valor === "") return "—"
  return BRL.format(Number(valor))
}

/** Data de hoje no fuso local, no formato da API (YYYY-MM-DD). */
export function hojeISO(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

export function atrasada(dataFim: string | null, percentual: number): boolean {
  return !!dataFim && dataFim < hojeISO() && percentual < 100
}

export function notificarErro(erro: unknown) {
  if (erro instanceof ApiError) {
    toast.error(erro.message, {
      description: erro.detalhes.length ? erro.detalhes.slice(0, 6).join("\n") : undefined,
    })
  } else {
    toast.error("Algo deu errado. Tente novamente.")
  }
}

export function pluralizar(n: number, singular: string, plural: string) {
  return `${n.toLocaleString("pt-BR")} ${n === 1 ? singular : plural}` // "20.000 tarefas", não "20000"
}
