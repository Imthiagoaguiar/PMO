import { useMutation } from "@tanstack/react-query"
import { Link2, Lock, Plus, X } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { useAtualizarProjeto, useDependencias } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import type { Dependencia, Projeto, Tarefa, TipoDependencia } from "@/lib/tipos"

export const NOME_TIPO: Record<TipoDependencia, string> = {
  inicio_apos_fim: "só começa depois que esta terminar",
  fim_apos_fim: "só termina depois que esta terminar",
}

/** Dependências do projeto indexadas, mais as tarefas conhecidas (as que já vieram do servidor). */
export function useMapaDependencias(projeto: Projeto) {
  const consulta = useDependencias(projeto.id)
  return useMemo(() => {
    const tarefas = new Map<number, Tarefa>()
    for (const f of projeto.fases) for (const t of f.tarefas) tarefas.set(t.id, t)
    const porSucessora = new Map<number, Dependencia[]>()
    const porPredecessora = new Map<number, Dependencia[]>()
    for (const d of consulta.data ?? []) {
      porSucessora.set(d.sucessora_id, [...(porSucessora.get(d.sucessora_id) ?? []), d])
      porPredecessora.set(d.predecessora_id, [...(porPredecessora.get(d.predecessora_id) ?? []), d])
    }
    return { tarefas, porSucessora, porPredecessora }
  }, [projeto.fases, consulta.data])
}

export type MapaDependencias = ReturnType<typeof useMapaDependencias>

function nome(mapa: MapaDependencias, id: number) {
  return mapa.tarefas.get(id)?.descricao ?? "outra tarefa"
}

/** O que trava a tarefa agora (vazio = nada trava). Mesmas regras do back (app/dependencias.py). */
export function bloqueios(mapa: MapaDependencias, tarefa: Tarefa): string[] {
  return (mapa.porSucessora.get(tarefa.id) ?? []).flatMap((d) => {
    const pred = mapa.tarefas.get(d.predecessora_id)
    if (pred && pred.percentual_conclusao >= 100) return []
    const situacao = pred ? ` (está em ${pred.percentual_conclusao}%)` : ""
    return d.tipo === "inicio_apos_fim"
      ? [`Só começa depois de “${nome(mapa, d.predecessora_id)}”${situacao}`]
      : [`Só termina depois de “${nome(mapa, d.predecessora_id)}”${situacao}`]
  })
}

/** Selo na linha do cronograma: cadeado se está esperando outra tarefa; elo se só tem dependências. */
export function SeloDependencia({ mapa, tarefa }: { mapa: MapaDependencias; tarefa: Tarefa }) {
  const antes = mapa.porSucessora.get(tarefa.id) ?? []
  const depois = mapa.porPredecessora.get(tarefa.id) ?? []
  if (!antes.length && !depois.length) return null
  const travas = bloqueios(mapa, tarefa)
  const linhas = [
    ...travas,
    ...antes.filter((d) => (mapa.tarefas.get(d.predecessora_id)?.percentual_conclusao ?? 0) >= 100)
      .map((d) => `Depende de “${nome(mapa, d.predecessora_id)}” (já concluída)`),
    ...depois.map((d) => `“${nome(mapa, d.sucessora_id)}” ${NOME_TIPO[d.tipo]}`),
  ]
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className={travas.length ? "ml-2 gap-1 border-amber-300 bg-amber-50 align-middle font-normal text-amber-800" : "ml-2 gap-1 align-middle font-normal"}
          tabIndex={0}
        >
          {travas.length ? <Lock className="size-3" /> : <Link2 className="size-3" />}
          {travas.length ? "Aguardando" : antes.length + depois.length}
          <span className="sr-only">: {linhas.join(". ")}</span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <ul className="grid gap-1">
          {linhas.map((l) => <li key={l}>{l}</li>)}
        </ul>
      </TooltipContent>
    </Tooltip>
  )
}

/** Seção do diálogo da tarefa: de quem ela depende, quem depende dela, adicionar e remover. */
export function DependenciasDaTarefa({ projeto, tarefa, podeEditar }: { projeto: Projeto; tarefa: Tarefa; podeEditar: boolean }) {
  const mapa = useMapaDependencias(projeto)
  const atualizar = useAtualizarProjeto(projeto.id)
  const [predecessora, setPredecessora] = useState("")
  const [tipo, setTipo] = useState<TipoDependencia>("inicio_apos_fim")
  const [ajustarDatas, setAjustarDatas] = useState(true)
  const antes = mapa.porSucessora.get(tarefa.id) ?? []
  const depois = mapa.porPredecessora.get(tarefa.id) ?? []
  const ja = new Set(antes.map((d) => d.predecessora_id))
  const opcoes = projeto.fases.flatMap((f) =>
    f.tarefas.filter((t) => t.id !== tarefa.id && !ja.has(t.id)).map((t) => ({ tarefa: t, fase: f.titulo })),
  )

  const criar = useMutation({
    mutationFn: () =>
      api<{ reprogramadas: Tarefa[] }>(`/projetos/${projeto.id}/dependencias`, {
        method: "POST",
        json: { predecessora_id: Number(predecessora), sucessora_id: tarefa.id, tipo, ajustar_datas: ajustarDatas },
      }),
    onSuccess: (r) => {
      atualizar()
      setPredecessora("")
      toast.success("Dependência criada.", {
        description: r.reprogramadas.length
          ? `${fmt.pluralizar(r.reprogramadas.length, "tarefa teve", "tarefas tiveram")} as datas empurradas para depois.`
          : undefined,
      })
    },
    onError: fmt.notificarErro,
  })
  const remover = useMutation({
    mutationFn: (d: Dependencia) => api(`/projetos/${projeto.id}/dependencias/${d.id}`, { method: "DELETE" }),
    onSuccess: () => {
      atualizar()
      toast.success("Dependência removida.")
    },
    onError: fmt.notificarErro,
  })

  return (
    <section aria-label="Dependências" className="grid gap-2 rounded-md border p-3 text-sm">
      <h3 className="font-medium">Dependências</h3>
      {antes.length === 0 && depois.length === 0 && (
        <p className="text-muted-foreground">Esta tarefa não depende de nenhuma outra.</p>
      )}
      {antes.map((d) => (
        <div key={d.id} className="flex items-center gap-2">
          <Link2 className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1">
            {d.tipo === "inicio_apos_fim" ? "Só começa depois de" : "Só termina depois de"} “{nome(mapa, d.predecessora_id)}”
            {mapa.tarefas.get(d.predecessora_id) && (
              <span className="text-muted-foreground"> · {mapa.tarefas.get(d.predecessora_id)!.percentual_conclusao}%</span>
            )}
          </span>
          {podeEditar && (
            <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remover dependência de ${nome(mapa, d.predecessora_id)}`}
              disabled={remover.isPending} onClick={() => remover.mutate(d)}>
              <X />
            </Button>
          )}
        </div>
      ))}
      {depois.length > 0 && (
        <p className="text-muted-foreground">
          Dependem desta: {depois.map((d) => `“${nome(mapa, d.sucessora_id)}”`).join(", ")}
        </p>
      )}

      {podeEditar && opcoes.length > 0 && (
        <div className="grid gap-2 border-t pt-2">
          <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
            <Select value={predecessora} onValueChange={setPredecessora}>
              <SelectTrigger className="w-full" aria-label="Tarefa da qual esta depende">
                <SelectValue placeholder="Depende de qual tarefa?" />
              </SelectTrigger>
              <SelectContent>
                {opcoes.map(({ tarefa: t, fase }) => (
                  <SelectItem key={t.id} value={String(t.id)}>
                    {t.descricao} <span className="text-muted-foreground">({fase})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={tipo} onValueChange={(v) => setTipo(v as TipoDependencia)}>
              <SelectTrigger aria-label="Tipo de dependência">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inicio_apos_fim">Começa depois</SelectItem>
                <SelectItem value="fim_apos_fim">Termina depois</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input type="checkbox" checked={ajustarDatas} onChange={(e) => setAjustarDatas(e.target.checked)} />
              Empurrar as datas desta tarefa (e das seguintes) para depois
            </label>
            <Button type="button" size="sm" variant="outline" disabled={!predecessora || criar.isPending} onClick={() => criar.mutate()}>
              <Plus /> Adicionar
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}
