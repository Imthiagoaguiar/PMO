import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query"
import { AlertTriangle, Download, Info, Pencil, Plus, Receipt, Trash2 } from "lucide-react"
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
import { api, baixarArquivo, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto, useOrcamento } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { CategoriaOrcamento, ItemOrcamento, Lancamento, Pagina, Projeto, TotalOrcamento } from "@/lib/tipos"

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
  const [lancando, setLancando] = useState<{ item?: ItemOrcamento; lancamento?: Lancamento } | null>(null)
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
        <LancamentoDialog projeto={projeto} itens={itens} item={lancando.item} lancamento={lancando.lancamento} aoFechar={() => setLancando(null)} />
      )}
      <ExcluirItem projeto={projeto} item={excluindoItem} aoFechar={() => setExcluindoItem(null)} />
    </>
  )

  if (itens.length === 0) {
    return (
      <>
        <Vazio titulo="Nenhum item no orçamento">
          Cadastre as linhas do orçamento (ex.: licenças, consultoria, equipamentos) com o valor orçado.
          Depois, lance os gastos realizados para acompanhar o orçado x realizado.
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
  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {podeEditar && (
          <>
            <Button size="sm" variant="outline" onClick={() => setLancando({})}>
              <Receipt /> Lançar gasto
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditandoItem("novo")}>
              <Plus /> Item
            </Button>
          </>
        )}
        <Button size="sm" variant="outline" onClick={exportar} disabled={exportando}>
          <Download /> {exportando ? "Exportando..." : "Exportar Excel"}
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Kpi titulo="Orçado (itens)" valor={fmt.moeda(r.total_orcado)}
          detalhe={r.orcamento_aprovado ? `Aprovado: ${fmt.moeda(r.orcamento_aprovado)}` : "Sem valor aprovado no projeto"} />
        <Kpi titulo="Realizado" valor={fmt.moeda(r.total_realizado)}
          detalhe={r.execucao !== null ? `${r.execucao.toLocaleString("pt-BR")}% do orçado` : undefined}
          barra={r.execucao !== null ? <BarraExecucao valor={r.execucao} /> : undefined} />
        <Kpi titulo="Saldo" valor={fmt.moeda(r.saldo)} destaque={saldoNegativo} detalhe={saldoNegativo ? "Acima do orçado" : "Ainda disponível"} />
        <Kpi titulo="Avanço do cronograma" valor={`${Math.round(r.avanco_fisico)}%`} detalhe="Média das tarefas" barra={<BarraProgresso valor={r.avanco_fisico} />} />
        <Kpi titulo="Previsão ao final" valor={fmt.moeda(r.previsao_final)}
          destaque={!!r.previsao_final && Number(r.previsao_final) > Number(r.total_orcado) * 1.05}
          detalhe={r.previsao_final ? "No ritmo atual: realizado ÷ avanço" : "Aparece com gasto e 10% do cronograma feito"} />
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

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Gasto acumulado x planejado</CardTitle>
            <CardDescription>Mês a mês. O planejado distribui o orçado por igual entre o início e o fim do cronograma.</CardDescription>
          </CardHeader>
          <CardContent>
            {r.curva.length >= 2 ? <CurvaAcumulada curva={r.curva} /> : (
              <p className="text-sm text-muted-foreground">
                A curva aparece com pelo menos dois meses de dados (lançamentos ou datas no cronograma).
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
              <TableHead className="text-right">Saldo</TableHead>
              <TableHead className="w-36">Execução</TableHead>
              {podeEditar && <TableHead className="w-28 pr-6"><span className="sr-only">Ações</span></TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {itens.map((i) => (
              <TableRow key={i.id}>
                <TableCell className="max-w-xs pl-6 whitespace-normal">
                  {i.descricao}
                  {i.estourado && <Badge variant="outline" className="ml-2 border-destructive/40 text-destructive">Estourou</Badge>}
                </TableCell>
                <TableCell>{NOME_CATEGORIA[i.categoria]}</TableCell>
                <TableCell className="text-muted-foreground">{i.pilar_nome ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.moeda(i.valor_orcado)}</TableCell>
                <TableCell className="text-right tabular-nums">{fmt.moeda(i.valor_realizado)}</TableCell>
                <TableCell className={cn("text-right tabular-nums", Number(i.saldo) < 0 && "font-medium text-destructive")}>
                  {fmt.moeda(i.saldo)}
                </TableCell>
                <TableCell>
                  {i.execucao !== null ? <BarraExecucao valor={i.execucao} /> : "—"}
                </TableCell>
                {podeEditar && (
                  <TableCell className="pr-6">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="icon-sm" aria-label={`Lançar gasto em ${i.descricao}`} onClick={() => setLancando({ item: i })}>
                        <Receipt />
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

      <Lancamentos projeto={projeto} itens={itens} podeEditar={podeEditar} aoEditar={(l) => setLancando({ lancamento: l })} />
      {dialogos}
    </div>
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

function Lancamentos({ projeto, itens, podeEditar, aoEditar }: {
  projeto: Projeto
  itens: ItemOrcamento[]
  podeEditar: boolean
  aoEditar: (l: Lancamento) => void
}) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [item, setItem] = useState(TODOS)
  const [categoria, setCategoria] = useState(TODOS)
  const [de, setDe] = useState("")
  const [ate, setAte] = useState("")
  const [offset, setOffset] = useState(0)
  const params = new URLSearchParams({ limite: String(POR_PAGINA), offset: String(offset) })
  if (item !== TODOS) params.set("item_id", item)
  if (categoria !== TODOS) params.set("categoria", categoria)
  if (de) params.set("de", de)
  if (ate) params.set("ate", ate)
  const lista = useQuery({
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

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b py-4">
        <CardTitle className="text-base">Lançamentos</CardTitle>
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
      {lista.isPending ? <Carregando /> : lista.isError ? (
        <div className="p-4"><ErroCarregar erro={lista.error} /></div>
      ) : lista.data.itens.length === 0 ? (
        <p className="px-6 py-4 text-sm text-muted-foreground">Nenhum lançamento {item !== TODOS || categoria !== TODOS || de || ate ? "com esses filtros" : "ainda"}.</p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-6">Data</TableHead>
                <TableHead>Item</TableHead>
                <TableHead>Descrição</TableHead>
                <TableHead>Documento</TableHead>
                <TableHead className="text-right">Valor</TableHead>
                <TableHead>Lançado por</TableHead>
                {podeEditar && <TableHead className="w-20 pr-6"><span className="sr-only">Ações</span></TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {lista.data.itens.map((l) => (
                <TableRow key={l.id}>
                  <TableCell className="pl-6 tabular-nums">{fmt.data(l.data)}</TableCell>
                  <TableCell>{nomes.get(l.item_id) ?? "—"}</TableCell>
                  <TableCell className="max-w-xs whitespace-normal">{l.descricao}</TableCell>
                  <TableCell className="text-muted-foreground">{l.documento ?? "—"}</TableCell>
                  <TableCell className={cn("text-right tabular-nums", Number(l.valor) < 0 && "text-emerald-700")}>
                    {fmt.moeda(l.valor)}{Number(l.valor) < 0 && <span className="sr-only"> (estorno)</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{l.criado_por?.nome ?? "—"}</TableCell>
                  {podeEditar && (
                    <TableCell className="pr-6">
                      <div className="flex justify-end gap-1">
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
            <Paginacao total={lista.data.total} limite={POR_PAGINA} offset={offset} aoMudar={setOffset} />
          </div>
        </>
      )}
    </Card>
  )
}

// ---------- Diálogos ----------

function ItemDialog({ projeto, item, aoFechar }: { projeto: Projeto; item?: ItemOrcamento; aoFechar: () => void }) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [descricao, setDescricao] = useState(item?.descricao ?? "")
  const [categoria, setCategoria] = useState<CategoriaOrcamento>(item?.categoria ?? "opex")
  const [pilar, setPilar] = useState(item?.pilar_id ? String(item.pilar_id) : SEM_PILAR)
  const [valor, setValor] = useState(item ? String(Number(item.valor_orcado)) : "")
  const salvar = useMutation({
    mutationFn: () => {
      const corpo = {
        descricao: descricao.trim(), categoria, pilar_id: pilar === SEM_PILAR ? null : Number(pilar),
        valor_orcado: Number(valor).toFixed(2),
      }
      return item
        ? api(`/projetos/${projeto.id}/orcamento/itens/${item.id}`, { method: "PATCH", json: { ...corpo, versao: item.versao } })
        : api(`/projetos/${projeto.id}/orcamento/itens`, { method: "POST", json: corpo })
    },
    onSuccess: () => {
      atualizar()
      toast.success(item ? "Item atualizado." : "Item adicionado ao orçamento.")
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
  const valido = descricao.trim() && valor !== "" && Number(valor) >= 0

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (valido) salvar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{item ? "Editar item" : "Novo item do orçamento"}</DialogTitle>
            <DialogDescription>Uma linha do orçamento com o valor previsto. Os gastos entram como lançamentos.</DialogDescription>
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
            <Campo label="Valor orçado (R$)" htmlFor="io-valor">
              <Input id="io-valor" type="number" min={0} step="0.01" inputMode="decimal" required value={valor}
                onChange={(e) => setValor(e.target.value)} />
            </Campo>
          </div>
          {projeto.pilares.length > 0 && (
            <Campo label="Pilar" dica="Ligado a um pilar, o orçado/realizado do pilar passa a vir daqui.">
              <Select value={pilar} onValueChange={setPilar}>
                <SelectTrigger className="w-full" aria-label="Pilar"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={SEM_PILAR}>Sem pilar</SelectItem>
                  {projeto.pilares.map((p) => <SelectItem key={p.id} value={String(p.id)}>{p.nome}</SelectItem>)}
                </SelectContent>
              </Select>
            </Campo>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>Cancelar</Button>
            <Button type="submit" disabled={!valido || salvar.isPending}>{salvar.isPending ? "Salvando..." : "Salvar"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function LancamentoDialog({ projeto, itens, item, lancamento, aoFechar }: {
  projeto: Projeto
  itens: ItemOrcamento[]
  item?: ItemOrcamento
  lancamento?: Lancamento
  aoFechar: () => void
}) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [itemId, setItemId] = useState(String(lancamento?.item_id ?? item?.id ?? itens[0]?.id ?? ""))
  const [data, setData] = useState(lancamento?.data ?? fmt.hojeISO())
  const [descricao, setDescricao] = useState(lancamento?.descricao ?? "")
  const [documento, setDocumento] = useState(lancamento?.documento ?? "")
  const [valor, setValor] = useState(lancamento ? String(Math.abs(Number(lancamento.valor))) : "")
  const [estorno, setEstorno] = useState(lancamento ? Number(lancamento.valor) < 0 : false)
  const salvar = useMutation({
    mutationFn: () => {
      const corpo = {
        item_id: Number(itemId), data, descricao: descricao.trim(), documento: documento.trim() || null,
        valor: ((estorno ? -1 : 1) * Number(valor)).toFixed(2),
      }
      return lancamento
        ? api(`/projetos/${projeto.id}/orcamento/lancamentos/${lancamento.id}`, { method: "PATCH", json: corpo })
        : api(`/projetos/${projeto.id}/orcamento/lancamentos`, { method: "POST", json: corpo })
    },
    onSuccess: () => {
      atualizar()
      toast.success(lancamento ? "Lançamento atualizado." : estorno ? "Estorno lançado." : "Gasto lançado.")
      aoFechar()
    },
    onError: fmt.notificarErro,
  })
  const valido = itemId && data && descricao.trim() && Number(valor) > 0

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (valido) salvar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{lancamento ? "Editar lançamento" : "Lançar gasto"}</DialogTitle>
            <DialogDescription>O valor realizado de um item, na data em que aconteceu.</DialogDescription>
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
            <Input id="l-descricao" required maxLength={200} autoFocus placeholder="Ex.: Parcela 1 de 3" value={descricao}
              onChange={(e) => setDescricao(e.target.value)} />
          </Campo>
          <div className="grid grid-cols-2 gap-4">
            <Campo label="Data" htmlFor="l-data">
              <Input id="l-data" type="date" required value={data} onChange={(e) => setData(e.target.value)} />
            </Campo>
            <Campo label="Valor (R$)" htmlFor="l-valor">
              <Input id="l-valor" type="number" min={0.01} step="0.01" inputMode="decimal" required value={valor}
                onChange={(e) => setValor(e.target.value)} />
            </Campo>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={estorno} onChange={(e) => setEstorno(e.target.checked)} />
            É um estorno (devolução: diminui o realizado)
          </label>
          <Campo label="Documento (opcional)" htmlFor="l-documento" dica="Nota fiscal, pedido de compra...">
            <Input id="l-documento" maxLength={60} value={documento} onChange={(e) => setDocumento(e.target.value)} />
          </Campo>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>Cancelar</Button>
            <Button type="submit" disabled={!valido || salvar.isPending}>{salvar.isPending ? "Salvando..." : "Salvar"}</Button>
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
            {excluir.isPending ? "Excluindo..." : item?.lancamentos ? "Excluir item e lançamentos" : "Excluir item"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
