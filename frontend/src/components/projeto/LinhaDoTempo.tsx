import { useMutation } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { useRef, useState, type PointerEvent as EventoPonteiro } from "react"
import { toast } from "sonner"

import { corFase, Vazio } from "@/components/comum"
import { MarcoDialog, NOME_FORMA, SimboloMarco } from "@/components/projeto/Marcos"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto, useMarcos } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { Marco, Projeto } from "@/lib/tipos"

const DIA = 86_400_000
const MAX_BARRAS = 300
const ROTULO = "14rem" // coluna dos nomes; a área do gráfico é o resto

function emDias(iso: string) {
  const [a, m, d] = iso.split("-").map(Number)
  return Date.UTC(a, m - 1, d) / DIA
}

function paraISO(dia: number) {
  return new Date(dia * DIA).toISOString().slice(0, 10)
}

const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

/**
 * Gantt (uma barra por tarefa com início e término) + marcos: pins, bandeiras, formas e faixas.
 * Quem edita arrasta o marco para mudar a data (um clique sem arrastar abre a edição).
 */
export function LinhaDoTempo({ projeto, podeEditar = false }: { projeto: Projeto; podeEditar?: boolean }) {
  const marcos = useMarcos(projeto.id).data ?? []
  const atualizar = useAtualizarProjeto(projeto.id)
  const [editando, setEditando] = useState<Marco | "novo" | null>(null)
  const [arrasto, setArrasto] = useState<{ id: number; delta: number } | null>(null)
  const area = useRef<HTMLDivElement>(null)

  const mover = useMutation({
    mutationFn: ({ marco, delta }: { marco: Marco; delta: number }) =>
      api<Marco>(`/projetos/${projeto.id}/marcos/${marco.id}`, {
        method: "PATCH",
        json: {
          data: paraISO(emDias(marco.data) + delta),
          ...(marco.data_fim ? { data_fim: paraISO(emDias(marco.data_fim) + delta) } : {}),
          versao: marco.versao,
        },
      }),
    onSuccess: (m) => {
      atualizar()
      toast.success(`“${m.titulo}” agora em ${fmt.data(m.data)}.`)
    },
    onError: (erro) => {
      fmt.notificarErro(erro)
      if (ehConflitoDeVersao(erro)) atualizar()
    },
    onSettled: () => setArrasto(null),
  })

  const todas = projeto.fases.flatMap((fase, iFase) =>
    fase.tarefas
      .filter((t) => t.data_inicio && t.data_fim)
      .map((t) => ({ tarefa: t, fase, iFase, inicio: emDias(t.data_inicio!), fim: emDias(t.data_fim!) + 1 })),
  )
  // Uma barra por tarefa: acima de algumas centenas o gráfico fica ilegível e pesado para o navegador.
  const linhas = todas.length > MAX_BARRAS ? [...todas].sort((a, b) => a.inicio - b.inicio).slice(0, MAX_BARRAS) : todas
  const semDatas = projeto.fases.reduce((n, f) => n + f.tarefas.filter((t) => !t.data_inicio || !t.data_fim).length, 0)
  const parcial = linhas.length < todas.length || projeto.fases.some((f) => f.tarefas.length < f.total_tarefas)

  const dialogo = editando && (
    <MarcoDialog projetoId={projeto.id} marco={editando === "novo" ? undefined : editando} aoFechar={() => setEditando(null)} />
  )

  if (linhas.length === 0 && marcos.length === 0) {
    return (
      <>
        <Vazio titulo="Sem tarefas com datas nem marcos">
          Defina início e término nas tarefas para vê-las aqui
          {podeEditar ? ", ou marque um evento importante (pré-entrega, go-live...)." : "."}
          {podeEditar && (
            <div>
              <Button size="sm" variant="outline" className="mt-3" onClick={() => setEditando("novo")}>
                <Plus /> Adicionar marco
              </Button>
            </div>
          )}
        </Vazio>
        {dialogo}
      </>
    )
  }

  const inicios = [...linhas.map((l) => l.inicio), ...marcos.map((m) => emDias(m.data))]
  const fins = [...linhas.map((l) => l.fim), ...marcos.map((m) => emDias(m.data_fim ?? m.data) + 1)]
  const inicio = Math.min(...inicios)
  const fim = Math.max(...fins, inicio + 14)
  const total = Math.max(fim - inicio, 1)
  const posicao = (dia: number) => `${((dia - inicio) / total) * 100}%`
  const largura = (dias: number) => `max(${(dias / total) * 100}%, 4px)`

  // Eixo: início, término e o 1º dia de cada mês no meio (sem marcas encostadas umas nas outras).
  const rotuloDia = (dia: number) => {
    const d = new Date(dia * DIA)
    return `${d.getUTCDate()}/${MESES[d.getUTCMonth()]}`
  }
  const marcas: { dia: number; rotulo: string }[] = [{ dia: inicio, rotulo: rotuloDia(inicio) }]
  const cursor = new Date(inicio * DIA)
  cursor.setUTCDate(1)
  cursor.setUTCMonth(cursor.getUTCMonth() + 1)
  while (cursor.getTime() / DIA < fim) {
    const dia = cursor.getTime() / DIA
    marcas.push({ dia, rotulo: `${MESES[cursor.getUTCMonth()]}/${String(cursor.getUTCFullYear()).slice(2)}` })
    cursor.setUTCMonth(cursor.getUTCMonth() + 1)
  }
  marcas.push({ dia: fim - 1, rotulo: rotuloDia(fim - 1) })
  const espacoMinimo = total * 0.07
  const eixo = marcas.filter((m, i) => i === 0 || i === marcas.length - 1 || (m.dia - marcas[i - 1].dia >= espacoMinimo && fim - 1 - m.dia >= espacoMinimo))
  const hoje = emDias(fmt.hojeISO())

  /** Arrastar: guarda o x inicial e converte o deslocamento em dias pela largura da área. */
  function comecarArrasto(e: EventoPonteiro<HTMLButtonElement>, marco: Marco) {
    if (!podeEditar || mover.isPending) return
    const pixels = area.current?.getBoundingClientRect().width ?? 0
    if (!pixels) return
    const x0 = e.clientX
    const alvo = e.currentTarget
    alvo.setPointerCapture(e.pointerId)
    let delta = 0
    const aoMover = (ev: PointerEvent) => {
      delta = Math.round(((ev.clientX - x0) / pixels) * total)
      setArrasto({ id: marco.id, delta })
    }
    const aoSoltar = () => {
      alvo.removeEventListener("pointermove", aoMover)
      alvo.removeEventListener("pointerup", aoSoltar)
      alvo.removeEventListener("pointercancel", aoSoltar)
      if (delta !== 0) mover.mutate({ marco, delta })
      else {
        setArrasto(null)
        setEditando(marco)
      }
    }
    alvo.addEventListener("pointermove", aoMover)
    alvo.addEventListener("pointerup", aoSoltar)
    alvo.addEventListener("pointercancel", aoSoltar)
  }

  const deslocamento = (m: Marco) => (arrasto?.id === m.id ? arrasto.delta : 0)
  const faixas = marcos.filter((m) => m.forma === "faixa" && m.data_fim)
  const pontuais = marcos.filter((m) => m.forma !== "faixa" || !m.data_fim)

  return (
    <div className="rounded-lg border bg-card p-4">
      {podeEditar && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">Arraste um marco para mudar a data; clique para editar.</p>
          <Button size="sm" variant="outline" onClick={() => setEditando("novo")}>
            <Plus /> Adicionar marco
          </Button>
        </div>
      )}
      <div className="overflow-x-auto">
        <div className="min-w-[720px]">
          <div className="grid gap-3" style={{ gridTemplateColumns: `${ROTULO} 1fr` }}>
            <div />
            <div className="relative h-6 border-b text-xs text-muted-foreground">
              {eixo.map((m, i) => (
                <span
                  key={m.dia}
                  className={cn("absolute top-0 whitespace-nowrap", i === 0 ? "" : i === eixo.length - 1 ? "-translate-x-full" : "-translate-x-1/2")}
                  style={{ left: posicao(i === eixo.length - 1 ? m.dia + 1 : m.dia) }}
                >
                  {m.rotulo}
                </span>
              ))}
            </div>
          </div>

          {marcos.length > 0 && (
            <div className="mt-2 grid items-center gap-3" style={{ gridTemplateColumns: `${ROTULO} 1fr` }}>
              <div className="text-sm font-medium text-muted-foreground">Marcos</div>
              <div ref={area} className="relative h-9 rounded bg-muted/40" aria-label="Marcos da linha do tempo">
                {marcos.map((m) => {
                  const d0 = emDias(m.data) + deslocamento(m)
                  const periodo = m.forma === "faixa" && m.data_fim
                  const d1 = periodo ? emDias(m.data_fim!) + deslocamento(m) + 1 : d0 + 1
                  const quando = fmt.data(paraISO(d0)) + (periodo ? ` a ${fmt.data(paraISO(d1 - 1))}` : "")
                  return (
                    <Tooltip key={m.id}>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          aria-label={`${m.titulo}: ${NOME_FORMA[m.forma]} em ${quando}${podeEditar ? ". Enter para editar" : ""}`}
                          className={cn(
                            "absolute top-1/2 -translate-y-1/2 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            podeEditar ? "cursor-grab touch-none active:cursor-grabbing" : "cursor-default",
                            arrasto?.id === m.id && "z-10 opacity-80",
                          )}
                          style={periodo ? { left: posicao(d0), width: largura(d1 - d0), height: 12 } : { left: `calc(${posicao(d0 + 0.5)} - 10px)` }}
                          onPointerDown={(e) => comecarArrasto(e, m)}
                          onKeyDown={(e) => {
                            if (podeEditar && e.key === "Enter") setEditando(m)
                          }}
                        >
                          {periodo ? (
                            <span className="block h-full w-full rounded" style={{ background: `var(--marco-${m.cor})` }} />
                          ) : (
                            <SimboloMarco forma={m.forma} cor={m.cor} tamanho={20} />
                          )}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-xs">
                        <p className="font-medium">{m.titulo}</p>
                        <p>{quando}</p>
                        {m.descricao && <p className="mt-1 opacity-90">{m.descricao}</p>}
                      </TooltipContent>
                    </Tooltip>
                  )
                })}
              </div>
            </div>
          )}

          <div className="relative mt-2 grid gap-1.5">
            {/* Faixas (períodos) e linhas dos marcos atravessam as tarefas, atrás das barras. */}
            <div className="pointer-events-none absolute inset-y-0 right-0" style={{ left: `calc(${ROTULO} + 0.75rem)` }}>
              {faixas.map((m) => {
                const d0 = emDias(m.data) + deslocamento(m)
                const d1 = emDias(m.data_fim!) + deslocamento(m) + 1
                return (
                  <div key={m.id} className="absolute inset-y-0 opacity-10" style={{ left: posicao(d0), width: largura(d1 - d0), background: `var(--marco-${m.cor})` }} />
                )
              })}
              {pontuais.map((m) => (
                <div
                  key={m.id}
                  className="absolute inset-y-0 border-l border-dashed"
                  style={{ left: posicao(emDias(m.data) + deslocamento(m) + 0.5), borderColor: `var(--marco-${m.cor})` }}
                />
              ))}
            </div>

            {linhas.map(({ tarefa, fase, iFase, inicio: i, fim: f }) => (
              <div key={tarefa.id} className="grid items-center gap-3" style={{ gridTemplateColumns: `${ROTULO} 1fr` }}>
                <div className="truncate text-sm" title={`${fase.titulo}: ${tarefa.descricao}`}>
                  {tarefa.descricao}
                </div>
                <div className="relative h-6">
                  {eixo.slice(1, -1).map((m) => (
                    <span key={m.dia} className="absolute inset-y-0 border-l border-dashed border-border" style={{ left: posicao(m.dia) }} />
                  ))}
                  <div
                    className={cn("absolute inset-y-1 overflow-hidden rounded", corFase(iFase), "opacity-35")}
                    style={{ left: posicao(i), width: largura(f - i) }}
                    title={`${fmt.data(tarefa.data_inicio)} a ${fmt.data(tarefa.data_fim)} · ${tarefa.percentual_conclusao}%`}
                  />
                  <div
                    className={cn("pointer-events-none absolute inset-y-1 rounded", corFase(iFase))}
                    style={{ left: posicao(i), width: `max(${(((f - i) / total) * tarefa.percentual_conclusao)}%, 0px)` }}
                  />
                </div>
              </div>
            ))}
            {hoje >= inicio && hoje <= fim && (
              <div className="pointer-events-none absolute inset-y-0 right-0" style={{ left: `calc(${ROTULO} + 0.75rem)` }}>
                <div className="absolute inset-y-0 border-l-2 border-destructive/70" style={{ left: posicao(hoje) }}>
                  <span className="absolute -top-5 -translate-x-1/2 rounded bg-destructive px-1 text-[10px] font-medium text-white">hoje</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {projeto.fases.map((f, i) => (
          <span key={f.id} className="flex items-center gap-1.5">
            <span className={cn("size-2.5 rounded-sm", corFase(i))} /> {f.titulo}
          </span>
        ))}
        <span>Parte escura = % concluído.</span>
        {semDatas > 0 && <span>{semDatas} tarefa(s) sem datas não aparecem aqui.</span>}
        {parcial && (
          <span>
            Projeto grande: mostrando as {linhas.length} primeiras tarefas por data de início. O cronograma completo está no Excel exportado.
          </span>
        )}
      </div>

      {marcos.length > 0 && (
        <ul className="mt-4 grid gap-1 border-t pt-3 text-sm" aria-label="Lista de marcos">
          {marcos.map((m) => (
            <li key={m.id} className="flex items-center gap-2">
              <SimboloMarco forma={m.forma} cor={m.cor} tamanho={16} />
              <span className="w-44 shrink-0 tabular-nums text-muted-foreground">
                {fmt.data(m.data)}{m.data_fim ? ` a ${fmt.data(m.data_fim)}` : ""}
              </span>
              <span className="font-medium">{m.titulo}</span>
              {m.descricao && <span className="truncate text-muted-foreground">— {m.descricao}</span>}
              {podeEditar && (
                <Button variant="link" size="sm" className="ml-auto h-auto p-0" onClick={() => setEditando(m)}>
                  Editar
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {dialogo}
    </div>
  )
}
