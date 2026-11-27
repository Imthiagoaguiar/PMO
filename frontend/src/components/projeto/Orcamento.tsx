import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query"
import { AlertTriangle, CalendarClock, CheckCircle2, Download, Info, Loader2, MessageSquareWarning, Pencil, Plus, Receipt, Trash2 } from "lucide-react"
import { useState, type FormEvent, type ReactNode } from "react"
import { toast } from "sonner"

import { BarraProgresso, Campo, Carregando, ErroCarregar, Paginacao, Vazio } from "@/components/comum"
import { BarrasOrcadoRealizado, CurvaAcumulada } from "@/components/projeto/GraficosOrcamento"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api, baixarArquivo, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto, useOrcamento } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { CategoriaOrcamento, ItemOrcamento, Lancamento, Pagina, Projeto, ResumoOrcamento, TotalOrcamento } from "@/lib/tipos"

const NOME_CATEGORIA: Record<CategoriaOrcamento, string> = { capex: "CAPEX", opex: "OPEX" }
const SEM_PILAR = "sem-pilar"
const TODOS = "todos"
const POR_PAGINA = 20

type Agrupamento = "item" | "categoria" | "pilar"

export function Orcamento({ projeto, podeEditar }: { projeto: Projeto; podeEditar: boolean }) {
  const resumo = useOrcamento(projeto.id)
  const [agrupar, setAgrupar] = useState<Agrupamento>("item")
  const [editandoItem, setEditandoItem] = useState<ItemOrcamento | "novo" | null>(null)
  const [excluindoItem, setExcluindoItem] = useState<ItemOrcamento | null>(null)
  const [lancando, setLancando] = useState<{ projetado: boolean; item?: ItemOrcamento; lancamento?: Lancamento } | null>(null)
  const [confirmando, setConfirmando] = useState<Lancamento | null>(null)
  const [exportando, setExportando] = useState(false)

  if (resumo.isPending) return <Carregando />
  if (resumo.isError) return <ErroCarregar erro={resumo.error} />
  const r = resumo.data
  const itens = r.itens

  async function exportar() {
    setExportando(true)
    try {
      await baixarArquivo(`/projetos/${projeto.id}/orcamento/exportar`, "orcamento.xlsx")
    } catch (erro) {
      fmt.notificarErro(erro)
    } finally {
      setExportando(false)
    }
  }

  const grupos: TotalOrcamento[] =
    agrupar === "categoria" ? r.por_categoria.filter((c) => Number(c.orcado) || Number(c.realizado))
    : agrupar === "pilar" ? r.por_pilar
    : itens.map((i) => ({ chave: String(i.id), nome: i.descricao, orcado: i.valor_orcado, realizado: i.valor_realizado }))

  const dialogos = (
    <>
      {editandoItem && (
        <ItemDialog projeto={projeto} item={editandoItem === "novo" ? undefined : editandoItem} aoFechar={() => setEditandoItem(null)} />
      )}
      {lancando && (
        <LancamentoDialog projeto={projeto} itens={itens} projetado={lancando.projetado} item={lancando.item}
          lancamento={lancando.lancamento} aoFechar={() => setLancando(null)} />
      )}
      {confirmando && (
        <ConfirmarDialog projeto={projeto} itens={itens} lancamento={confirmando} aoFechar={() => setConfirmando(null)} />
      )}
      <ExcluirItem projeto={projeto} item={excluindoItem} aoFechar={() => setExcluindoItem(null)} />
    </>
  )

  if (itens.length === 0) {
    return (
      <>
        <Vazio titulo="Nenhum item no orçamento">
          Cadastre as linhas do orçamento (ex.: licenças, consultoria, equipamentos) com o valor orçado, total ou mês a mês.
          Depois, lance os gastos realizados e projete os futuros para acompanhar o orçado x realizado e a previsão final.
          {podeEditar && (
            <div>
              <Button size="sm" variant="outline" className="mt-3" onClick={() => setEditandoItem("novo")}>
                <Plus /> Adicionar item
              </Button>
            </div>
          )}
        </Vazio>
        {dialogos}
      </>
    )
  }

  const saldoNegativo = Number(r.saldo) < 0
  const previsaoAcima = Number(r.total_orcado) > 0 && Number(r.previsao_final) > Number(r.total_orcado)
  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {podeEditar && (
          <>
            <Button size="sm" variant="outline" onClick={() => setLancando({ projetado: false })}>
              <Receipt /> Lançar gasto
            </Button>
            <Button size="sm" variant="outline" onClick={() => setLancando({ projetado: true })}>
              <CalendarClock /> Projetar gasto futuro
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditandoItem("novo")}>
              <Plus /> Item
            </Button>
          </>
        )}
        <Button carregando={exportando} size="sm" variant="outline" onClick={exportar} disabled={exportando}>
          <Download /> {exportando ? "Exportando..." : "Exportar Excel"}
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi titulo="Orçado (itens)" valor={fmt.moeda(r.total_orcado)}
          detalhe={r.orcamento_aprovado ? `Aprovado: ${fmt.moeda(r.orcamento_aprovado)}` : "Sem valor aprovado no projeto"} />
        <Kpi titulo="Realizado" valor={fmt.moeda(r.total_realizado)}
          detalhe={r.execucao !== null ? `${r.execucao.toLocaleString("pt-BR")}% do orçado` : undefined}
          barra={r.execucao !== null ? <BarraExecucao valor={r.execucao} /> : undefined} />
        <Kpi titulo="Projetado (futuro)" valor={fmt.moeda(r.total_projetado)} detalhe="Gastos previstos que ainda não aconteceram" />
        <Kpi titulo="Previsão final" valor={fmt.moeda(r.previsao_final)} destaque={previsaoAcima}
          detalhe={`Realizado + projetado${r.previsao_ritmo ? ` · no ritmo atual: ${fmt.moeda(r.previsao_ritmo)}` : ""}`} />
        <Kpi titulo="Saldo" valor={fmt.moeda(r.saldo)} destaque={saldoNegativo} detalhe={saldoNegativo ? "Acima do orçado" : "Ainda disponível"} />
        <Kpi titulo="Avanço do cronograma" valor={`${Math.round(r.avanco_fisico)}%`} detalhe="Média das tarefas" barra={<BarraProgresso valor={r.avanco_fisico} />} />
      </div>

      {r.alertas.length > 0 && (
        <ul className="grid gap-2" aria-label="Alertas do orçamento">
          {r.alertas.map((a) => (
            <li key={a.mensagem} className={cn(
              "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
              a.nivel === "alto" ? "border-destructive/40 bg-destructive/5" : a.nivel === "medio" ? "border-amber-300 bg-amber-50" : "bg-muted/40",
            )}>
              {a.nivel === "info" ? <Info className="mt-0.5 size-4 shrink-0" /> : (
                <AlertTriangle className={cn("mt-0.5 size-4 shrink-0", a.nivel === "alto" ? "text-destructive" : "text-amber-600")} />
              )}
              <span>
                <span className="sr-only">{a.nivel === "alto" ? "Alerta grave: " : a.nivel === "medio" ? "Atenção: " : "Nota: "}</span>
                {a.mensagem}
              </span>
            </li>
          ))}
        </ul>
      )}

      <CapexOpex r={r} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Gasto acumulado x planejado</CardTitle>
            <CardDescription>
              Mês a mês. O planejado segue os valores por mês dos itens; itens com valor único são distribuídos por igual no
              período do cronograma. O previsto soma os gastos projetados.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {r.curva.length >= 2 ? <CurvaAcumulada curva={r.curva} /> : (
              <p className="text-sm text-muted-foreground">
                A curva aparece com pelo menos dois meses de dados (lançamentos, meses dos itens ou datas no cronograma).
              </p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-2">
            <div className="grid gap-1.5">
              <CardTitle className="text-base">Orçado x realizado</CardTitle>
              <CardDescription>Passe o mouse para ver os valores.</CardDescription>
            </div>
            <Select value={agrupar} onValueChange={(v) => setAgrupar(v as Agrupamento)}>
              <SelectTrigger className="w-36" aria-label="Agrupar por" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="item">Por item</SelectItem>
                <SelectItem value="categoria">Por categoria</SelectItem>
                <SelectItem value="pilar">Por pilar</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            <BarrasOrcadoRealizado grupos={grupos} />
          </CardContent>
        </Card>
      </div>

      <Card className="gap-0 py-0">
        <CardHeader className="border-b py-4">
          <CardTitle className="text-base">Itens do orçamento</CardTitle>
        </CardHeader>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-6">Item</TableHead>
              <TableHead>Categoria</TableHead>
              <TableHead>Pilar</TableHead>
              <TableHead className="text-right">Orçado</TableHead>
              <TableHead className="text-right">Realizado</TableHead>
              <TableHead className="text-right">Projetado</TableHead>
              <TableHead className="text-right">Previsão</TableHead>
              <TableHead className="text-right">Saldo</TableHead>
              <TableHead className="w-32">Execução</TableHead>
              {podeEditar && <TableHead className="w-36 pr-6"><span className="sr-only">Ações</span></TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {itens.map((i) => (
              <TableRow key={i.id}>
                <TableCell className="max-w-xs pl-6 whitespace-normal">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {i.descricao}
                    {i.meses.length > 0 && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge variant="outline" tabIndex={0}>{i.meses.length === 1 ? fmt.mesAno(i.meses[0].mes) : `${i.meses.length} meses`}</Badge>
                        </TooltipTrigger>
                        <TooltipContent>
                          {i.meses.map((m) => <p key={m.mes}>{fmt.mesAno(m.mes)}: {fmt.moeda(m.valor)}</p>)}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    {i.estourado && <Badge variant="outline" className="border-destructive/40 text-destructive">Estourou</Badge>}
                  </div>
                  {i.estourado && (
                    <p className={cn("mt-1 text-xs", i.justificativa ? "text-muted-foreground" : "text-destructive")}>
                      {i.justificativa ? <>Por quê: {i.justificativa}</> : "Sem justificativa registrada"}
                    </p>
                  )}
                </TableCell>
                <TableCell>{NOME_CATEGORIA[i.categoria]}</TableCell>
                <TableCell className="text-muted-foreground">{i.pilar_nome ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.moeda(i.valor_orcado)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.moeda(i.valor_realizado)}</TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">{Number(i.valor_projetado) ? fmt.moeda(i.valor_projetado) : "—"}</TableCell>
                <TableCell className={cn("text-right tabular-nums", Number(i.previsao) > Number(i.valor_orcado) && "font-medium text-destructive")}>
                  {fmt.moeda(i.previsao)}
                </TableCell>
                <TableCell className={cn("text-right tabular-nums", Number(i.saldo) < 0 && "font-medium text-destructive")}>
                  {fmt.moeda(i.saldo)}
                </TableCell>
                <TableCell>
                  {i.execucao !== null ? <BarraExecucao valor={i.execucao} /> : "—"}
                </TableCell>
                {podeEditar && (
                  <TableCell className="pr-6">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="icon-sm" aria-label={`Lançar gasto em ${i.descricao}`} onClick={() => setLancando({ projetado: false, item: i })}>
                        <Receipt />
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label={`Projetar gasto futuro em ${i.descricao}`} onClick={() => setLancando({ projetado: true, item: i })}>
                        <CalendarClock />
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label={`Editar ${i.descricao}`} onClick={() => setEditandoItem(i)}>
                        <Pencil />
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label={`Excluir ${i.descricao}`} onClick={() => setExcluindoItem(i)}>
                        <Trash2 />
                      </Button>
                    </div>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Lancamentos projeto={projeto} itens={itens} podeEditar={podeEditar}
        aoEditar={(l) => setLancando({ projetado: l.projetado, lancamento: l })} aoConfirmar={setConfirmando} />
      {dialogos}
    </div>
  )
}

/** CAPEX x OPEX: orçado, realizado, projetado e previsão de cada categoria. */
function CapexOpex({ r }: { r: ResumoOrcamento }) {
  const linhas = [
    ...r.por_categoria,
    { chave: "total", nome: "Total", orcado: r.total_orcado, realizado: r.total_realizado, projetado: r.total_projetado },
  ]
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b py-4">
        <CardTitle className="text-base">CAPEX x OPEX</CardTitle>
        <CardDescription>Investimento (CAPEX) e despesa (OPEX) separados: o que já foi gasto, o que está projetado e onde vai fechar.</CardDescription>
      </CardHeader>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-6">Categoria</TableHead>
            <TableHead className="text-right">Orçado</TableHead>
            <TableHead className="text-right">Realizado</TableHead>
            <TableHead className="text-right">Projetado</TableHead>
            <TableHead className="text-right">Previsão final</TableHead>
            <TableHead className="pr-6 text-right">Saldo previsto</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {linhas.map((c) => {
            const previsao = Number(c.realizado) + Number(c.projetado ?? 0)
            const saldo = Number(c.orcado) - previsao
            return (
              <TableRow key={c.chave} className={cn(c.chave === "total" && "bg-muted/40 font-medium")}>
                <TableCell className="pl-6">{c.chave === "capex" ? "CAPEX (investimento)" : c.chave === "opex" ? "OPEX (despesa)" : c.nome}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.moeda(c.orcado)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.moeda(c.realizado)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.moeda(c.projetado ?? 0)}</TableCell>
                <TableCell className={cn("text-right tabular-nums", Number(c.orcado) > 0 && previsao > Number(c.orcado) && "text-destructive")}>
                  {fmt.moeda(previsao)}
                </TableCell>
                <TableCell className={cn("pr-6 text-right tabular-nums", saldo < 0 && "text-destructive")}>{fmt.moeda(saldo)}</TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </Card>
  )
}

/** Execução financeira: até 100% azul; acima disso vermelha (gastou mais que o orçado), com o valor real. */
function BarraExecucao({ valor }: { valor: number }) {
  const estourou = valor > 100
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full", estourou ? "bg-destructive" : "bg-primary")} style={{ width: `${Math.min(100, Math.max(0, valor))}%` }} />
      </div>
      <span className={cn("w-12 text-right text-xs tabular-nums", estourou ? "font-medium text-destructive" : "text-muted-foreground")}>
        {Math.round(valor)}%
      </span>
    </div>
  )
}

function Kpi({ titulo, valor, detalhe, destaque, barra }: { titulo: string; valor: string; detalhe?: string; destaque?: boolean; barra?: ReactNode }) {
  return (
    <Card className="gap-1 py-4">
      <CardHeader className="px-4">
        <CardDescription>{titulo}</CardDescription>
        <CardTitle className={cn("text-xl tabular-nums", destaque && "text-destructive")}>{valor}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-1.5 px-4 text-xs text-muted-foreground">
        {barra}
        {detalhe}
      </CardContent>
    </Card>
  )
}

// ---------- Lançamentos ----------

type Lista = "realizados" | "projetados"

function Lancamentos({ projeto, itens, podeEditar, aoEditar, aoConfirmar }: {
  projeto: Projeto
  itens: ItemOrcamento[]
  podeEditar: boolean
  aoEditar: (l: Lancamento) => void
  aoConfirmar: (l: Lancamento) => void
}) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [lista, setLista] = useState<Lista>("realizados")
  const [item, setItem] = useState(TODOS)
  const [categoria, setCategoria] = useState(TODOS)
  const [de, setDe] = useState("")
  const [ate, setAte] = useState("")
  const [offset, setOffset] = useState(0)
  const projetados = lista === "projetados"
  const params = new URLSearchParams({ limite: String(POR_PAGINA), offset: String(offset), projetado: String(projetados) })
  if (item !== TODOS) params.set("item_id", item)
  if (categoria !== TODOS) params.set("categoria", categoria)
  if (de) params.set("de", de)
  if (ate) params.set("ate", ate)
  const consulta = useQuery({
    queryKey: ["projeto", projeto.id, "orcamento", "lancamentos", params.toString()],
    queryFn: () => api<Pagina<Lancamento>>(`/projetos/${projeto.id}/orcamento/lancamentos?${params}`),
    placeholderData: keepPreviousData,
  })
  const nomes = new Map(itens.map((i) => [i.id, i.descricao]))
  const excluir = useMutation({
    mutationFn: (l: Lancamento) => api(`/projetos/${projeto.id}/orcamento/lancamentos/${l.id}`, { method: "DELETE" }),
    onSuccess: () => {
      atualizar()
      toast.success("Lançamento excluído.")
    },
    onError: fmt.notificarErro,
  })
  const filtrar = (mudar: () => void) => {
    mudar()
    setOffset(0)
  }
  const filtrado = item !== TODOS || categoria !== TODOS || de || ate

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">Lançamentos</CardTitle>
          <div role="tablist" aria-label="Lançamentos" className="flex gap-1">
            {(["realizados", "projetados"] as const).map((v) => (
              <Button key={v} role="tab" aria-selected={lista === v} size="sm" variant={lista === v ? "default" : "outline"}
                onClick={() => filtrar(() => setLista(v))}>
                {v === "realizados" ? "Realizados" : "Projetados (futuros)"}
              </Button>
            ))}
          </div>
        </div>
        {projetados && (
          <CardDescription>
            Gastos que ainda vão acontecer. Não entram no realizado, mas somam na previsão final. Quando acontecerem, confirme.
          </CardDescription>
        )}
        <div className="mt-2 flex flex-wrap items-end gap-2 text-sm">
          <Select value={item} onValueChange={(v) => filtrar(() => setItem(v))}>
            <SelectTrigger className="w-52" aria-label="Filtrar por item" size="sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={TODOS}>Todos os itens</SelectItem>
              {itens.map((i) => <SelectItem key={i.id} value={String(i.id)}>{i.descricao}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={categoria} onValueChange={(v) => filtrar(() => setCategoria(v))}>
            <SelectTrigger className="w-36" aria-label="Filtrar por categoria" size="sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={TODOS}>CAPEX e OPEX</SelectItem>
              <SelectItem value="capex">Só CAPEX</SelectItem>
              <SelectItem value="opex">Só OPEX</SelectItem>
            </SelectContent>
          </Select>
          <Input type="date" aria-label="A partir de" className="h-8 w-40" value={de} onChange={(e) => filtrar(() => setDe(e.target.value))} />
          <Input type="date" aria-label="Até" className="h-8 w-40" value={ate} onChange={(e) => filtrar(() => setAte(e.target.value))} />
        </div>
      </CardHeader>
      {consulta.isPending ? <Carregando /> : consulta.isError ? (
        <div className="p-4"><ErroCarregar erro={consulta.error} /></div>
      ) : consulta.data.itens.length === 0 ? (
        <p className="px-6 py-4 text-sm text-muted-foreground">
          {projetados ? "Nenhum gasto projetado" : "Nenhum lançamento"} {filtrado ? "com esses filtros" : "ainda"}.
        </p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-6">{projetados ? "Previsto para" : "Data"}</TableHead>
                <TableHead>Item</TableHead>
                <TableHead>Descrição</TableHead>
                <TableHead>Documento</TableHead>
                <TableHead className="text-right">Valor</TableHead>
                <TableHead>Lançado por</TableHead>
                {podeEditar && <TableHead className="w-28 pr-6"><span className="sr-only">Ações</span></TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {consulta.data.itens.map((l) => (
                <TableRow key={l.id}>
                  <TableCell className="pl-6 tabular-nums">{fmt.data(l.data)}</TableCell>
                  <TableCell>{nomes.get(l.item_id) ?? "—"}</TableCell>
                  <TableCell className="max-w-xs whitespace-normal">
                    {l.descricao}
                    {l.justificativa && (
                      <p className="mt-0.5 flex items-start gap-1 text-xs text-muted-foreground">
                        <MessageSquareWarning className="mt-0.5 size-3 shrink-0" aria-hidden />
                        <span><span className="sr-only">Justificativa do estouro: </span>{l.justificativa}</span>
                      </p>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{l.documento ?? "—"}</TableCell>
                  <TableCell className={cn("text-right tabular-nums", Number(l.valor) < 0 && "text-emerald-700")}>
                    {fmt.moeda(l.valor)}{Number(l.valor) < 0 && <span className="sr-only"> (estorno)</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{l.criado_por?.nome ?? "—"}</TableCell>
                  {podeEditar && (
                    <TableCell className="pr-6">
                      <div className="flex justify-end gap-1">
                        {l.projetado && (
                          <Button variant="ghost" size="icon-sm" aria-label={`Confirmar que “${l.descricao}” aconteceu`} onClick={() => aoConfirmar(l)}>
                            <CheckCircle2 />
                          </Button>
                        )}
                        <Button variant="ghost" size="icon-sm" aria-label="Editar lançamento" onClick={() => aoEditar(l)}><Pencil /></Button>
                        <Button variant="ghost" size="icon-sm" aria-label="Excluir lançamento" disabled={excluir.isPending}
                          onClick={() => window.confirm(`Excluir o lançamento “${l.descricao}” (${fmt.moeda(l.valor)})?`) && excluir.mutate(l)}>
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="px-6 pb-4">
            <Paginacao total={consulta.data.total} limite={POR_PAGINA} offset={offset} aoMudar={setOffset} />
          </div>
        </>
      )}
    </Card>
  )
}

// ---------- Diálogos ----------

/** Campo de justificativa, obrigatório quando o gasto deixa o item acima do orçado. */
function CampoJustificativa({ id, item, gasto, valor, aoMudar }: {
  id: string
  item: ItemOrcamento
  gasto: number
  valor: string
  aoMudar: (v: string) => void
}) {
  return (
    <div className="grid gap-1.5 rounded-md border border-destructive/40 bg-destructive/5 p-3">
      <label htmlFor={id} className="text-sm font-medium">Por que está estourando? (obrigatório)</label>
      <p className="text-xs text-muted-foreground">
        Com este gasto, “{item.descricao}” fica em {fmt.moeda(gasto)}, acima dos {fmt.moeda(item.valor_orcado)} orçados.
      </p>
      <Textarea id={id} required maxLength={1000} value={valor} onChange={(e) => aoMudar(e.target.value)}
        placeholder="Ex.: reajuste de 10% do fornecedor no contrato renovado" />
    </div>
  )
}

function proximoMes(mes: string): string {
  const [a, m] = mes.split("-").map(Number)
  return m === 12 ? `${a + 1}-01` : `${a}-${String(m + 1).padStart(2, "0")}`
}

function ItemDialog({ projeto, item, aoFechar }: { projeto: Projeto; item?: ItemOrcamento; aoFechar: () => void }) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [descricao, setDescricao] = useState(item?.descricao ?? "")
  const [categoria, setCategoria] = useState<CategoriaOrcamento>(item?.categoria ?? "opex")
  const [pilar, setPilar] = useState(item?.pilar_id ? String(item.pilar_id) : SEM_PILAR)
  const [modo, setModo] = useState<"total" | "mensal">(item?.meses.length ? "mensal" : "total")
  const [valor, setValor] = useState(item ? String(Number(item.valor_orcado)) : "")
  const [meses, setMeses] = useState<{ mes: string; valor: string }[]>(
    item?.meses.length ? item.meses.map((m) => ({ mes: m.mes, valor: String(Number(m.valor)) })) : [{ mes: fmt.hojeISO().slice(0, 7), valor: "" }],
  )
  const gastoAtual = item ? Number(item.valor_realizado) : 0
  const [gasto, setGasto] = useState(item ? String(gastoAtual) : "")
  const [justificativa, setJustificativa] = useState("")

  const totalMeses = meses.reduce((t, m) => t + (Number(m.valor) || 0), 0)
  const orcado = modo === "mensal" ? totalMeses : Number(valor) || 0
  const novoGasto = gasto === "" ? gastoAtual : Number(gasto)
  const mudouGasto = !!item && gasto !== "" && Math.abs(novoGasto - gastoAtual) >= 0.005
  const pedeJustificativa = mudouGasto && novoGasto > gastoAtual && novoGasto > orcado
  const mesesValidos = meses.length > 0 && meses.every((m) => /^\d{4}-\d{2}$/.test(m.mes) && m.valor !== "" && Number(m.valor) >= 0)
    && new Set(meses.map((m) => m.mes)).size === meses.length

  const salvar = useMutation({
    mutationFn: () => {
      const corpo: Record<string, unknown> = { descricao: descricao.trim(), categoria, pilar_id: pilar === SEM_PILAR ? null : Number(pilar) }
      if (modo === "mensal") corpo.meses = meses.map((m) => ({ mes: m.mes, valor: Number(m.valor).toFixed(2) }))
      else {
        corpo.valor_orcado = Number(valor).toFixed(2)
        if (item?.meses.length) corpo.meses = []
      }
      if (mudouGasto) {
        corpo.valor_realizado = novoGasto.toFixed(2)
        if (justificativa.trim()) corpo.justificativa = justificativa.trim()
      }
      return item
        ? api(`/projetos/${projeto.id}/orcamento/itens/${item.id}`, { method: "PATCH", json: { ...corpo, versao: item.versao } })
        : api(`/projetos/${projeto.id}/orcamento/itens`, { method: "POST", json: corpo })
    },
    onSuccess: () => {
      atualizar()
      toast.success(item ? (mudouGasto ? "Item atualizado e valor gasto ajustado." : "Item atualizado.") : "Item adicionado ao orçamento.")
      aoFechar()
    },
    onError: (erro) => {
      fmt.notificarErro(erro)
      if (ehConflitoDeVersao(erro)) {
        atualizar()
        aoFechar()
      }
    },
  })
  const valido = descricao.trim() && (modo === "mensal" ? mesesValidos : valor !== "" && Number(valor) >= 0)
    && (gasto === "" || Number(gasto) >= 0) && (!pedeJustificativa || justificativa.trim())

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (valido) salvar.mutate()
  }

  function mudarMes(indice: number, campo: "mes" | "valor", novo: string) {
    setMeses((atual) => atual.map((m, i) => (i === indice ? { ...m, [campo]: novo } : m)))
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{item ? "Editar item" : "Novo item do orçamento"}</DialogTitle>
            <DialogDescription>Uma linha do orçamento com o valor previsto, total ou mês a mês. Os gastos entram como lançamentos.</DialogDescription>
          </DialogHeader>
          <Campo label="Descrição" htmlFor="io-descricao">
            <Input id="io-descricao" required maxLength={150} autoFocus placeholder="Ex.: Licenças do ERP" value={descricao}
              onChange={(e) => setDescricao(e.target.value)} />
          </Campo>
          <div className="grid grid-cols-2 gap-4">
            <Campo label="Categoria">
              <Select value={categoria} onValueChange={(v) => setCategoria(v as CategoriaOrcamento)}>
                <SelectTrigger className="w-full" aria-label="Categoria"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="capex">CAPEX (investimento)</SelectItem>
                  <SelectItem value="opex">OPEX (despesa)</SelectItem>
                </SelectContent>
              </Select>
            </Campo>
            {projeto.pilares.length > 0 && (
              <Campo label="Pilar">
                <Select value={pilar} onValueChange={setPilar}>
                  <SelectTrigger className="w-full" aria-label="Pilar"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={SEM_PILAR}>Sem pilar</SelectItem>
                    {projeto.pilares.map((p) => <SelectItem key={p.id} value={String(p.id)}>{p.nome}</SelectItem>)}
                  </SelectContent>
                </Select>
              </Campo>
            )}
          </div>

          <fieldset className="grid gap-3">
            <legend className="mb-1.5 text-sm font-medium">Valor orçado</legend>
            <div role="radiogroup" aria-label="Como orçar" className="flex gap-1">
              {(["total", "mensal"] as const).map((m) => (
                <Button key={m} type="button" role="radio" aria-checked={modo === m} size="sm" variant={modo === m ? "default" : "outline"}
                  onClick={() => setModo(m)}>
                  {m === "total" ? "Valor total" : "Mês a mês"}
                </Button>
              ))}
            </div>
            {modo === "total" ? (
              <Input id="io-valor" aria-label="Valor orçado (R$)" type="number" min={0} step="0.01" inputMode="decimal" required value={valor}
                onChange={(e) => setValor(e.target.value)} />
            ) : (
              <div className="grid gap-2">
                {meses.map((m, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input type="month" aria-label={`Mês ${i + 1}`} className="w-52 shrink-0" required value={m.mes} onChange={(e) => mudarMes(i, "mes", e.target.value)} />
                    <Input type="number" aria-label={`Valor do mês ${i + 1} (R$)`} min={0} step="0.01" inputMode="decimal" required
                      placeholder="R$" value={m.valor} onChange={(e) => mudarMes(i, "valor", e.target.value)} />
                    <Button type="button" variant="ghost" size="icon-sm" aria-label={`Remover mês ${i + 1}`} disabled={meses.length === 1}
                      onClick={() => setMeses((atual) => atual.filter((_, j) => j !== i))}>
                      <Trash2 />
                    </Button>
                  </div>
                ))}
                <div className="flex items-center justify-between gap-2">
                  <Button type="button" variant="outline" size="sm" disabled={meses.length >= 120}
                    onClick={() => setMeses((atual) => [...atual, { mes: proximoMes(atual[atual.length - 1]?.mes ?? fmt.hojeISO().slice(0, 7)), valor: "" }])}>
                    <Plus /> Mês
                  </Button>
                  <span className="text-sm tabular-nums">Total: <span className="font-medium">{fmt.moeda(totalMeses)}</span></span>
                </div>
                {new Set(meses.map((m) => m.mes)).size !== meses.length && (
                  <p className="text-xs text-destructive">Cada mês só pode aparecer uma vez.</p>
                )}
              </div>
            )}
          </fieldset>

          {item && (
            <Campo label="Valor gasto até agora (R$)" htmlFor="io-gasto"
              dica={mudouGasto
                ? `Vamos lançar um ajuste de ${fmt.moeda(novoGasto - gastoAtual)} com a data de hoje (fica no histórico).`
                : `Soma dos lançamentos: ${fmt.moeda(gastoAtual)}. Corrija aqui se o valor gasto estiver errado.`}>
              <Input id="io-gasto" type="number" min={0} step="0.01" inputMode="decimal" value={gasto} onChange={(e) => setGasto(e.target.value)} />
            </Campo>
          )}
          {item && pedeJustificativa && (
            <CampoJustificativa id="io-justificativa" item={{ ...item, valor_orcado: String(orcado) }} gasto={novoGasto} valor={justificativa} aoMudar={setJustificativa} />
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>Cancelar</Button>
            <Button carregando={salvar.isPending} type="submit" disabled={!valido || salvar.isPending}>{salvar.isPending ? "Salvando..." : "Salvar"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function primeiroDiaDoProximoMes(): string {
  const [a, m] = fmt.hojeISO().split("-").map(Number)
  return m === 12 ? `${a + 1}-01-01` : `${a}-${String(m + 1).padStart(2, "0")}-01`
}

function LancamentoDialog({ projeto, itens, projetado, item, lancamento, aoFechar }: {
  projeto: Projeto
  itens: ItemOrcamento[]
  projetado: boolean
  item?: ItemOrcamento
  lancamento?: Lancamento
  aoFechar: () => void
}) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [itemId, setItemId] = useState(String(lancamento?.item_id ?? item?.id ?? itens[0]?.id ?? ""))
  const [data, setData] = useState(lancamento?.data ?? (projetado ? primeiroDiaDoProximoMes() : fmt.hojeISO()))
  const [descricao, setDescricao] = useState(lancamento?.descricao ?? "")
  const [documento, setDocumento] = useState(lancamento?.documento ?? "")
  const [valor, setValor] = useState(lancamento ? String(Math.abs(Number(lancamento.valor))) : "")
  const [estorno, setEstorno] = useState(lancamento ? Number(lancamento.valor) < 0 : false)
  const [justificativa, setJustificativa] = useState(lancamento?.justificativa ?? "")

  const escolhido = itens.find((i) => String(i.id) === itemId)
  const valorAssinado = (estorno ? -1 : 1) * (Number(valor) || 0)
  // Quanto o item fica gastando depois deste lançamento (o valor antigo sai, se for o mesmo item).
  const antes = escolhido ? Number(escolhido.valor_realizado) - (lancamento && !lancamento.projetado && lancamento.item_id === escolhido.id ? Number(lancamento.valor) : 0) : 0
  const depois = antes + valorAssinado
  const pedeJustificativa = !projetado && !!escolhido && valorAssinado > 0
    && depois > Number(escolhido.valor_orcado) && depois > Number(escolhido.valor_realizado)

  const salvar = useMutation({
    mutationFn: () => {
      const corpo: Record<string, unknown> = {
        item_id: Number(itemId), data, descricao: descricao.trim(), documento: documento.trim() || null,
        valor: valorAssinado.toFixed(2),
      }
      if (justificativa.trim() || lancamento?.justificativa) corpo.justificativa = justificativa.trim() || null
      if (!lancamento) corpo.projetado = projetado
      return lancamento
        ? api(`/projetos/${projeto.id}/orcamento/lancamentos/${lancamento.id}`, { method: "PATCH", json: corpo })
        : api(`/projetos/${projeto.id}/orcamento/lancamentos`, { method: "POST", json: corpo })
    },
    onSuccess: () => {
      atualizar()
      toast.success(lancamento ? "Lançamento atualizado." : projetado ? "Gasto projetado." : estorno ? "Estorno lançado." : "Gasto lançado.")
      aoFechar()
    },
    onError: fmt.notificarErro,
  })
  const valido = itemId && data && descricao.trim() && Number(valor) > 0 && (!pedeJustificativa || justificativa.trim())

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (valido) salvar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{lancamento ? (projetado ? "Editar gasto projetado" : "Editar lançamento") : projetado ? "Projetar gasto futuro" : "Lançar gasto"}</DialogTitle>
            <DialogDescription>
              {projetado
                ? "Um gasto que ainda vai acontecer. Não entra no realizado, mas soma na previsão final."
                : "O valor realizado de um item, na data em que aconteceu."}
            </DialogDescription>
          </DialogHeader>
          <Campo label="Item">
            <Select value={itemId} onValueChange={setItemId}>
              <SelectTrigger className="w-full" aria-label="Item do orçamento"><SelectValue /></SelectTrigger>
              <SelectContent>
                {itens.map((i) => <SelectItem key={i.id} value={String(i.id)}>{i.descricao}</SelectItem>)}
              </SelectContent>
            </Select>
          </Campo>
          <Campo label="Descrição" htmlFor="l-descricao">
            <Input id="l-descricao" required maxLength={200} autoFocus placeholder={projetado ? "Ex.: Parcela 2 de 3 (prevista)" : "Ex.: Parcela 1 de 3"}
              value={descricao} onChange={(e) => setDescricao(e.target.value)} />
          </Campo>
          <div className="grid grid-cols-2 gap-4">
            <Campo label={projetado ? "Previsto para" : "Data"} htmlFor="l-data">
              <Input id="l-data" type="date" required value={data} onChange={(e) => setData(e.target.value)} />
            </Campo>
            <Campo label="Valor (R$)" htmlFor="l-valor">
              <Input id="l-valor" type="number" min={0.01} step="0.01" inputMode="decimal" required value={valor}
                onChange={(e) => setValor(e.target.value)} />
            </Campo>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={estorno} onChange={(e) => setEstorno(e.target.checked)} />
            É um estorno (devolução: diminui o {projetado ? "previsto" : "realizado"})
          </label>
          <Campo label="Documento (opcional)" htmlFor="l-documento" dica="Nota fiscal, pedido de compra...">
            <Input id="l-documento" maxLength={60} value={documento} onChange={(e) => setDocumento(e.target.value)} />
          </Campo>
          {pedeJustificativa && escolhido && (
            <CampoJustificativa id="l-justificativa" item={escolhido} gasto={depois} valor={justificativa} aoMudar={setJustificativa} />
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>Cancelar</Button>
            <Button carregando={salvar.isPending} type="submit" disabled={!valido || salvar.isPending}>{salvar.isPending ? "Salvando..." : "Salvar"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** O gasto projetado aconteceu: vira realizado, com a data e o valor reais. */
function ConfirmarDialog({ projeto, itens, lancamento, aoFechar }: {
  projeto: Projeto
  itens: ItemOrcamento[]
  lancamento: Lancamento
  aoFechar: () => void
}) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [data, setData] = useState(fmt.hojeISO())
  const [valor, setValor] = useState(String(Number(lancamento.valor)))
  const [justificativa, setJustificativa] = useState(lancamento.justificativa ?? "")
  const item = itens.find((i) => i.id === lancamento.item_id)
  const depois = item ? Number(item.valor_realizado) + (Number(valor) || 0) : 0
  const pedeJustificativa = !!item && Number(valor) > 0 && depois > Number(item.valor_orcado)
  const confirmar = useMutation({
    mutationFn: () => api(`/projetos/${projeto.id}/orcamento/lancamentos/${lancamento.id}/confirmar`, {
      method: "POST", json: { data, valor: Number(valor).toFixed(2), ...(justificativa.trim() ? { justificativa: justificativa.trim() } : {}) },
    }),
    onSuccess: () => {
      atualizar()
      toast.success("Gasto confirmado: agora conta no realizado.")
      aoFechar()
    },
    onError: fmt.notificarErro,
  })
  const valido = data && Number(valor) !== 0 && valor !== "" && (!pedeJustificativa || justificativa.trim())

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (valido) confirmar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Confirmar gasto</DialogTitle>
            <DialogDescription>
              “{lancamento.descricao}” aconteceu? Ele sai dos projetados e passa a contar no realizado. Ajuste a data e o valor reais.
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-4">
            <Campo label="Data em que aconteceu" htmlFor="c-data">
              <Input id="c-data" type="date" required value={data} onChange={(e) => setData(e.target.value)} />
            </Campo>
            <Campo label="Valor real (R$)" htmlFor="c-valor">
              <Input id="c-valor" type="number" step="0.01" inputMode="decimal" required value={valor} onChange={(e) => setValor(e.target.value)} />
            </Campo>
          </div>
          {pedeJustificativa && item && (
            <CampoJustificativa id="c-justificativa" item={item} gasto={depois} valor={justificativa} aoMudar={setJustificativa} />
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>Cancelar</Button>
            <Button carregando={confirmar.isPending} type="submit" disabled={!valido || confirmar.isPending}>
              {confirmar.isPending ? "Confirmando..." : "Confirmar gasto"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function ExcluirItem({ projeto, item, aoFechar }: { projeto: Projeto; item: ItemOrcamento | null; aoFechar: () => void }) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const excluir = useMutation({
    mutationFn: (i: ItemOrcamento) =>
      api(`/projetos/${projeto.id}/orcamento/itens/${i.id}${i.lancamentos ? "?excluir_lancamentos=true" : ""}`, { method: "DELETE" }),
    onSuccess: () => {
      atualizar()
      toast.success("Item excluído.")
      aoFechar()
    },
    onError: fmt.notificarErro,
  })
  return (
    <AlertDialog open={!!item} onOpenChange={(abrir) => !abrir && aoFechar()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Excluir “{item?.descricao}”?</AlertDialogTitle>
          <AlertDialogDescription>
            {item?.lancamentos
              ? `Os ${item.lancamentos} lançamento(s) deste item (${fmt.moeda(item.valor_realizado)}) também serão excluídos.`
              : "O item não tem lançamentos."}{" "}
            A exclusão fica registrada no histórico.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-white hover:bg-destructive/90"
            onClick={(e) => {
              e.preventDefault()
              if (item) excluir.mutate(item)
            }}
          >
            {excluir.isPending && <Loader2 className="animate-spin" aria-hidden />}
            {excluir.isPending ? "Excluindo..." : item?.lancamentos ? "Excluir item e lançamentos" : "Excluir item"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
