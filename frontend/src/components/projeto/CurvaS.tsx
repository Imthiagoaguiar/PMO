import { Carregando, ErroCarregar, Vazio } from "@/components/comum"
import { GraficoLinhas } from "@/components/projeto/GraficoLinhas"
import { reaisCurto } from "@/components/projeto/GraficosOrcamento"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useCurvaS } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"

function Kpi({ titulo, valor, detalhe, tom }: { titulo: string; valor: string; detalhe: string; tom?: "bom" | "ruim" }) {
  return (
    <Card className="gap-1 py-4">
      <CardHeader className="px-4">
        <CardDescription>{titulo}</CardDescription>
        <CardTitle className={cn("text-2xl tabular-nums", tom === "ruim" && "text-destructive", tom === "bom" && "text-emerald-700")}>{valor}</CardTitle>
      </CardHeader>
      <CardContent className="px-4 text-xs text-muted-foreground">{detalhe}</CardContent>
    </Card>
  )
}

const rotulo = (iso: string) => fmt.data(iso).slice(0, 5) + "/" + iso.slice(2, 4)

export function CurvaS({ projetoId }: { projetoId: number }) {
  const consulta = useCurvaS(projetoId)
  if (consulta.isPending) return <Carregando texto="Calculando a curva S..." />
  if (consulta.isError) return <ErroCarregar erro={consulta.error} />
  const c = consulta.data
  if (c.pontos.length === 0) {
    return (
      <Vazio titulo="Sem tarefas com datas">
        A curva S compara o planejado com o realizado ao longo do tempo: defina início e término nas tarefas.
        {c.tarefas_sem_datas > 0 && ` (${fmt.pluralizar(c.tarefas_sem_datas, "tarefa está", "tarefas estão")} sem datas.)`}
      </Vazio>
    )
  }
  const hoje = fmt.hojeISO()
  const indiceHoje = c.pontos.findIndex((p) => p.data >= hoje)
  const desvio = c.desvio_pp ?? 0
  const situacao = desvio > 0 ? "adiantado" : desvio < 0 ? "atrasado" : "em dia"

  return (
    <div className="grid gap-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi titulo="Planejado até hoje" valor={c.planejado_hoje === null ? "—" : `${Math.round(c.planejado_hoje)}%`} detalhe="Pelas datas das tarefas (tarefas longas pesam mais)" />
        <Kpi titulo="Realizado até hoje" valor={c.realizado_hoje === null ? "—" : `${Math.round(c.realizado_hoje)}%`} detalhe="Pelo andamento registrado das tarefas" />
        <Kpi titulo="Desvio" valor={c.desvio_pp === null ? "—" : `${desvio > 0 ? "+" : ""}${desvio.toLocaleString("pt-BR")} pontos`}
          detalhe={c.desvio_pp === null ? "Ainda não começou" : `O projeto está ${situacao}`} tom={desvio < 0 ? "ruim" : desvio > 0 ? "bom" : undefined} />
        <Kpi titulo="IDP (índice de prazo)" valor={c.idp === null ? "—" : c.idp.toLocaleString("pt-BR")}
          detalhe="Realizado ÷ planejado: abaixo de 1 = atrasado" tom={c.idp !== null && c.idp < 0.9 ? "ruim" : c.idp !== null && c.idp >= 1 ? "bom" : undefined} />
      </div>

      <div className={cn("grid gap-4", c.com_orcamento && "lg:grid-cols-2")}>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Físico: % do cronograma</CardTitle>
            <CardDescription>Quanto do trabalho deveria estar feito x quanto foi feito, semana a semana.</CardDescription>
          </CardHeader>
          <CardContent>
            <GraficoLinhas rotuloAria="Curva S física: planejado e realizado acumulados em porcentagem" maximoFixo={100}
              pontos={c.pontos.map((p) => ({ rotulo: rotulo(p.data), planejado: p.planejado_pct, realizado: p.realizado_pct }))}
              formatarEixo={(v) => `${v}%`} formatarValor={(v) => `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`} indiceHoje={indiceHoje} />
          </CardContent>
        </Card>
        {c.com_orcamento && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Financeiro: R$ acumulado</CardTitle>
              <CardDescription>
                O orçado distribuído pela curva física planejada x os gastos lançados.
                {c.realizado_rs_hoje !== null && ` Até hoje: ${fmt.moeda(c.realizado_rs_hoje)} de ${fmt.moeda(c.planejado_rs_hoje)} previstos.`}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <GraficoLinhas rotuloAria="Curva S financeira: planejado e realizado acumulados em reais"
                pontos={c.pontos.map((p) => ({ rotulo: rotulo(p.data), planejado: p.planejado_rs === null ? null : Number(p.planejado_rs), realizado: p.realizado_rs === null ? null : Number(p.realizado_rs) }))}
                formatarEixo={reaisCurto} formatarValor={(v) => fmt.moeda(v)} indiceHoje={indiceHoje} />
            </CardContent>
          </Card>
        )}
      </div>
      {c.tarefas_sem_datas > 0 && (
        <p className="text-xs text-muted-foreground">{fmt.pluralizar(c.tarefas_sem_datas, "tarefa sem datas fica", "tarefas sem datas ficam")} fora da curva.</p>
      )}
    </div>
  )
}
