import { COR_FASE, Vazio } from "@/components/comum"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { Projeto } from "@/lib/tipos"

const DIA = 86_400_000
const MAX_BARRAS = 300

function emDias(iso: string) {
  const [a, m, d] = iso.split("-").map(Number)
  return Date.UTC(a, m - 1, d) / DIA
}

const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

/** Gráfico de Gantt simples: uma barra por tarefa com início e término definidos. */
export function LinhaDoTempo({ projeto }: { projeto: Projeto }) {
  const todas = projeto.fases.flatMap((fase, iFase) =>
    fase.tarefas
      .filter((t) => t.data_inicio && t.data_fim)
      .map((t) => ({ tarefa: t, fase, iFase, inicio: emDias(t.data_inicio!), fim: emDias(t.data_fim!) + 1 })),
  )
  // Uma barra por tarefa: acima de algumas centenas o gráfico fica ilegível e pesado para o navegador.
  const linhas = todas.length > MAX_BARRAS ? [...todas].sort((a, b) => a.inicio - b.inicio).slice(0, MAX_BARRAS) : todas
  const semDatas = projeto.fases.reduce((n, f) => n + f.tarefas.filter((t) => !t.data_inicio || !t.data_fim).length, 0)
  const parcial = linhas.length < todas.length || projeto.fases.some((f) => f.tarefas.length < f.total_tarefas)

  if (linhas.length === 0) {
    return (
      <Vazio titulo="Sem tarefas com datas">
        Defina início e término (ou a duração em dias úteis) nas tarefas para vê-las na linha do tempo.
      </Vazio>
    )
  }

  const inicio = Math.min(...linhas.map((l) => l.inicio))
  const fim = Math.max(...linhas.map((l) => l.fim))
  const total = Math.max(fim - inicio, 1)
  const posicao = (dia: number) => `${((dia - inicio) / total) * 100}%`

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

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="overflow-x-auto">
        <div className="min-w-[720px]">
          <div className="grid grid-cols-[14rem_1fr] gap-3">
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

          <div className="relative mt-2 grid gap-1.5">
            {linhas.map(({ tarefa, fase, iFase, inicio: i, fim: f }) => (
              <div key={tarefa.id} className="grid grid-cols-[14rem_1fr] items-center gap-3">
                <div className="truncate text-sm" title={`${fase.titulo}: ${tarefa.descricao}`}>
                  {tarefa.descricao}
                </div>
                <div className="relative h-6">
                  {eixo.slice(1, -1).map((m) => (
                    <span key={m.dia} className="absolute inset-y-0 border-l border-dashed border-border" style={{ left: posicao(m.dia) }} />
                  ))}
                  <div
                    className={cn("absolute inset-y-1 overflow-hidden rounded", COR_FASE[iFase], "opacity-35")}
                    style={{ left: posicao(i), width: `max(${((f - i) / total) * 100}%, 4px)` }}
                    title={`${fmt.data(tarefa.data_inicio)} a ${fmt.data(tarefa.data_fim)} · ${tarefa.percentual_conclusao}%`}
                  />
                  <div
                    className={cn("pointer-events-none absolute inset-y-1 rounded", COR_FASE[iFase])}
                    style={{ left: posicao(i), width: `max(${(((f - i) / total) * tarefa.percentual_conclusao)}%, 0px)` }}
                  />
                </div>
              </div>
            ))}
            {hoje >= inicio && hoje <= fim && (
              <div className="pointer-events-none absolute inset-y-0 right-0 left-[calc(14rem+0.75rem)]">
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
            <span className={cn("size-2.5 rounded-sm", COR_FASE[i])} /> {f.titulo}
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
    </div>
  )
}
