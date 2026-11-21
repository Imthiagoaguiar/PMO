import { useState } from "react"

import { Carregando, ErroCarregar, Vazio } from "@/components/comum"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useGargalos } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { GrupoGargalo } from "@/lib/tipos"

// As duas cores validadas do app (index.css): azul = já entregue (com atraso), laranja = ainda atrasada.
const COR_ENTREGUE = "var(--serie-realizado)"
const COR_ABERTA = "var(--serie-planejado)"
const MAX_BARRAS = 12

type Dimensao = "por_responsavel" | "por_pilar" | "por_fase"
const NOMES: Record<Dimensao, string> = { por_responsavel: "Por pessoa", por_pilar: "Por área (pilar)", por_fase: "Por fase" }

const atraso = (g: GrupoGargalo) => g.dias_atraso_entregues + g.dias_atraso_abertas

function Kpi({ titulo, valor, detalhe, destaque }: { titulo: string; valor: string; detalhe: string; destaque?: boolean }) {
  return (
    <Card className="gap-1 py-4">
      <CardHeader className="px-4">
        <CardDescription>{titulo}</CardDescription>
        <CardTitle className={cn("text-2xl tabular-nums", destaque && "text-destructive")}>{valor}</CardTitle>
      </CardHeader>
      <CardContent className="px-4 text-xs text-muted-foreground">{detalhe}</CardContent>
    </Card>
  )
}

export function Gargalos({ projetoId }: { projetoId: number }) {
  const consulta = useGargalos(projetoId)
  const [dimensao, setDimensao] = useState<Dimensao>("por_responsavel")

  if (consulta.isPending) return <Carregando texto="Calculando os gargalos..." />
  if (consulta.isError) return <ErroCarregar erro={consulta.error} />
  const g = consulta.data
  const grupos = g[dimensao]
  const comAtraso = grupos.filter((x) => atraso(x) > 0)
  const abertas = g.por_fase.reduce((s, x) => s + x.abertas_atrasadas, 0)

  if (grupos.every((x) => x.tarefas_com_prazo === 0)) {
    return (
      <Vazio titulo="Ainda não há o que medir">
        Os gargalos aparecem quando as tarefas têm prazo (término). O atraso conta a partir do prazo até a entrega (100%) ou até hoje.
      </Vazio>
    )
  }

  const maximo = Math.max(...comAtraso.map(atraso), 1)
  const pior = comAtraso[0]
  return (
    <div className="grid gap-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi titulo="Dias de atraso acumulados" valor={g.dias_atraso_total.toLocaleString("pt-BR")} destaque={g.dias_atraso_total > 0}
          detalhe="Somando entregas atrasadas e tarefas ainda vencidas" />
        <Kpi titulo="Pontualidade" valor={g.pontualidade === null ? "—" : `${Math.round(g.pontualidade)}%`}
          detalhe="Das tarefas entregues, quantas no prazo" />
        <Kpi titulo="Atrasadas agora" valor={String(abertas)} destaque={abertas > 0} detalhe="Prazo vencido e ainda abertas" />
        <Kpi titulo="Maior gargalo" valor={pior ? pior.nome : "Nenhum"}
          detalhe={pior ? `${atraso(pior)} dias de atraso (${NOMES[dimensao].toLowerCase()})` : "Nenhum atraso registrado"} />
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-2">
          <div className="grid gap-1.5">
            <CardTitle className="text-base">Onde o prazo escapou</CardTitle>
            <CardDescription>Dias de atraso acumulados, do maior para o menor. Passe o mouse para ver os números.</CardDescription>
          </div>
          <Select value={dimensao} onValueChange={(v) => setDimensao(v as Dimensao)}>
            <SelectTrigger className="w-44" aria-label="Agrupar por" size="sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              {(Object.keys(NOMES) as Dimensao[]).map((d) => <SelectItem key={d} value={d}>{NOMES[d]}</SelectItem>)}
            </SelectContent>
          </Select>
        </CardHeader>
        <CardContent className="grid gap-3">
          {comAtraso.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhum atraso: todas as entregas com prazo saíram em dia.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: COR_ENTREGUE }} /> Entregues com atraso</span>
                <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: COR_ABERTA }} /> Ainda atrasadas</span>
              </div>
              <ul className="grid gap-2.5">
                {comAtraso.slice(0, MAX_BARRAS).map((x) => (
                  <Tooltip key={x.nome}>
                    <TooltipTrigger asChild>
                      <li tabIndex={0} className="grid grid-cols-[minmax(6rem,12rem)_1fr_auto] items-center gap-3 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        <span className="truncate text-sm" title={x.nome}>{x.nome}</span>
                        <span className="flex h-3 gap-0.5">
                          {x.dias_atraso_entregues > 0 && (
                            <span className="h-full rounded-l" style={{ width: `${(x.dias_atraso_entregues / maximo) * 100}%`, background: COR_ENTREGUE }} />
                          )}
                          {x.dias_atraso_abertas > 0 && (
                            <span className="h-full rounded-r" style={{ width: `${(x.dias_atraso_abertas / maximo) * 100}%`, background: COR_ABERTA }} />
                          )}
                        </span>
                        <span className="text-xs tabular-nums text-muted-foreground">{atraso(x)} d</span>
                      </li>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p className="font-medium">{x.nome}</p>
                      <p>Entregues com atraso: {x.entregues_atrasadas} ({x.dias_atraso_entregues} dias)</p>
                      <p>Ainda atrasadas: {x.abertas_atrasadas} ({x.dias_atraso_abertas} dias)</p>
                      <p>Atraso médio: {x.atraso_medio ?? "—"} dias · pior: {x.pior_atraso} dias</p>
                    </TooltipContent>
                  </Tooltip>
                ))}
              </ul>
              {comAtraso.length > MAX_BARRAS && (
                <p className="text-xs text-muted-foreground">Mais {comAtraso.length - MAX_BARRAS} na tabela abaixo.</p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Card className="gap-0 py-0">
          <CardHeader className="border-b py-4"><CardTitle className="text-base">{NOMES[dimensao]}</CardTitle></CardHeader>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-6">Nome</TableHead>
                <TableHead className="text-right">No prazo</TableHead>
                <TableHead className="text-right">Com atraso</TableHead>
                <TableHead className="text-right">Atrasadas agora</TableHead>
                <TableHead className="text-right">Atraso médio</TableHead>
                <TableHead className="pr-6 text-right">Pontualidade</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {grupos.filter((x) => x.tarefas_com_prazo > 0).map((x) => (
                <TableRow key={x.nome}>
                  <TableCell className="pl-6">{x.nome}</TableCell>
                  <TableCell className="text-right tabular-nums">{x.entregues_no_prazo}</TableCell>
                  <TableCell className="text-right tabular-nums">{x.entregues_atrasadas}</TableCell>
                  <TableCell className={cn("text-right tabular-nums", x.abertas_atrasadas > 0 && "font-medium text-destructive")}>{x.abertas_atrasadas}</TableCell>
                  <TableCell className="text-right tabular-nums">{x.atraso_medio === null ? "—" : `${x.atraso_medio} d`}</TableCell>
                  <TableCell className="pr-6 text-right tabular-nums">{x.pontualidade === null ? "—" : `${Math.round(x.pontualidade)}%`}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>

        <Card className="gap-0 py-0">
          <CardHeader className="border-b py-4"><CardTitle className="text-base">Maiores atrasos</CardTitle></CardHeader>
          {g.piores.length === 0 ? <p className="px-6 py-4 text-sm text-muted-foreground">Nenhuma tarefa atrasada.</p> : (
            <ul className="divide-y">
              {g.piores.map((t) => (
                <li key={t.tarefa_id} className="flex items-start justify-between gap-3 px-6 py-2.5 text-sm">
                  <span className="min-w-0">
                    <span className="block truncate">{t.descricao}</span>
                    <span className="block text-xs text-muted-foreground">
                      {t.responsavel ?? "Sem responsável"} · {t.fase} · prazo {fmt.data(t.prazo)}
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block font-medium tabular-nums text-destructive">{t.dias_atraso} d</span>
                    <span className="block text-xs text-muted-foreground">{t.situacao === "aberta_atrasada" ? "ainda aberta" : "entregue"}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {g.entregues_sem_data > 0 && (
        <p className="text-xs text-muted-foreground">
          {fmt.pluralizar(g.entregues_sem_data, "tarefa concluída", "tarefas concluídas")} sem data de conclusão registrada
          (ex.: importadas já prontas) não entram nas contas.
        </p>
      )}
    </div>
  )
}
