import { useInfiniteQuery, useMutation } from "@tanstack/react-query"
import { CalendarClock, ListOrdered, MessageSquare, Pencil, Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { BarraProgresso, corFase, Vazio } from "@/components/comum"
import { SeloDependencia, useMapaDependencias, type MapaDependencias } from "@/components/projeto/Dependencias"
import { GerenciarFases } from "@/components/projeto/Fases"
import { ComentariosDialog } from "@/components/projeto/Comentarios"
import { TarefaDialog } from "@/components/projeto/TarefaDialog"
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
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { useAtualizarProjeto } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { Fase, Indicadores, Membro, Pagina, Projeto, Tarefa } from "@/lib/tipos"

interface Props {
  projeto: Projeto
  indicadores?: Indicadores
  membros: Membro[]
  podeEditar: boolean
}

const TODOS = "todos"
const SEM_PILAR = "sem-pilar"

export function Cronograma({ projeto, indicadores, membros, podeEditar }: Props) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [editando, setEditando] = useState<{ fase: Fase; tarefa?: Tarefa } | null>(null)
  const [excluindo, setExcluindo] = useState<Tarefa | null>(null)
  const [comentando, setComentando] = useState<Tarefa | null>(null)
  const nomes = new Map(membros.map((m) => [m.id, m.nome]))
  const nomesPilares = new Map(projeto.pilares.map((p) => [p.id, p.nome]))
  const [filtroPilar, setFiltroPilar] = useState(TODOS)
  const [gerenciandoFases, setGerenciandoFases] = useState(false)
  const dependencias = useMapaDependencias(projeto)
  const temDependencias = dependencias.porSucessora.size > 0

  const reprogramar = useMutation({
    mutationFn: () => api<{ reprogramadas: Tarefa[] }>(`/projetos/${projeto.id}/dependencias/reprogramar`, { method: "POST" }),
    onSuccess: (r) => {
      atualizar()
      toast.success(
        r.reprogramadas.length
          ? `${fmt.pluralizar(r.reprogramadas.length, "tarefa reprogramada", "tarefas reprogramadas")} para respeitar as dependências.`
          : "As datas já respeitam todas as dependências.",
      )
    },
    onError: fmt.notificarErro,
  })

  const excluir = useMutation({
    mutationFn: (t: Tarefa) => api(`/projetos/${projeto.id}/tarefas/${t.id}`, { method: "DELETE" }),
    onSuccess: () => {
      atualizar()
      toast.success("Tarefa excluída.")
      setExcluindo(null)
    },
    onError: fmt.notificarErro,
  })

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
      {projeto.pilares.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">Pilar:</span>
          <Select value={filtroPilar} onValueChange={setFiltroPilar}>
            <SelectTrigger className="w-52" aria-label="Filtrar por pilar">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={TODOS}>Todos os pilares</SelectItem>
              {projeto.pilares.map((p) => (
                <SelectItem key={p.id} value={String(p.id)}>
                  {p.nome}
                </SelectItem>
              ))}
              <SelectItem value={SEM_PILAR}>Sem pilar</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}
        {!projeto.seguir_ordem_fases && <span className="text-muted-foreground">Fases em paralelo (sem ordem obrigatória)</span>}
        {podeEditar && (
          <div className="ml-auto flex gap-2">
            {temDependencias && (
              <Button size="sm" variant="outline" onClick={() => reprogramar.mutate()} disabled={reprogramar.isPending}>
                <CalendarClock /> {reprogramar.isPending ? "Reprogramando..." : "Reprogramar pelas dependências"}
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => setGerenciandoFases(true)}>
              <ListOrdered /> Fases
            </Button>
          </div>
        )}
      </div>
      {projeto.fases.length === 0 && (
        <Vazio titulo="Este projeto ainda não tem fases">
          {podeEditar ? (
            <Button size="sm" variant="outline" className="mt-3" onClick={() => setGerenciandoFases(true)}>
              <Plus /> Criar as fases
            </Button>
          ) : "Quem edita o projeto pode criá-las."}
        </Vazio>
      )}
      {projeto.fases.map((fase, i) => (
        <CartaoFase
          key={fase.id}
          projetoId={projeto.id}
          fase={fase}
          indice={i}
          filtroPilar={filtroPilar}
          indicadores={indicadores}
          podeEditar={podeEditar}
          nomes={nomes}
          nomesPilares={nomesPilares}
          dependencias={dependencias}
          aoCriar={() => setEditando({ fase })}
          aoEditar={(t) => setEditando({ fase, tarefa: t })}
          aoExcluir={setExcluindo}
          aoComentar={setComentando}
        />
      ))}

      {editando && (
        <TarefaDialog
          projetoId={projeto.id}
          fases={projeto.fases}
          membros={membros}
          pilares={projeto.pilares}
          faseInicial={editando.fase}
          tarefa={editando.tarefa}
          projeto={projeto}
          aoFechar={() => setEditando(null)}
        />
      )}

      {gerenciandoFases && <GerenciarFases projeto={projeto} aoFechar={() => setGerenciandoFases(false)} />}

      {comentando && (
        <ComentariosDialog projeto={projeto} tarefa={comentando} aoFechar={() => setComentando(null)} />
      )}

      <AlertDialog open={!!excluindo} onOpenChange={(abrir) => !abrir && setExcluindo(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir tarefa?</AlertDialogTitle>
            <AlertDialogDescription>
              “{excluindo?.descricao}” e os comentários dela serão excluídos. A exclusão fica registrada no histórico.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={(e) => {
                e.preventDefault()
                if (excluindo) excluir.mutate(excluindo)
              }}
            >
              {excluir.isPending ? "Excluindo..." : "Excluir"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

const POR_PAGINA = 200

/**
 * Tarefas de uma fase: as que vieram no detalhe do projeto (até 200) e, se houver mais ou se
 * houver filtro de pilar, as do servidor em páginas (GET /projetos/{id}/tarefas).
 */
function useTarefasDaFase(projetoId: number, fase: Fase, filtro: string) {
  const completa = filtro === TODOS && fase.tarefas.length >= fase.total_tarefas
  const consulta = useInfiniteQuery({
    queryKey: ["projeto", projetoId, "tarefas", fase.id, filtro],
    enabled: !completa,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ fase_id: String(fase.id), limite: String(POR_PAGINA), offset: String(pageParam) })
      if (filtro === SEM_PILAR) params.set("sem_pilar", "true")
      else if (filtro !== TODOS) params.set("pilar_id", filtro)
      return api<Pagina<Tarefa>>(`/projetos/${projetoId}/tarefas?${params}`)
    },
    getNextPageParam: (ultima) => {
      const proxima = ultima.offset + ultima.itens.length
      return proxima < ultima.total ? proxima : undefined
    },
    ...(filtro === TODOS
      ? { initialData: { pages: [{ itens: fase.tarefas, total: fase.total_tarefas, limite: POR_PAGINA, offset: 0 }], pageParams: [0] } }
      : {}),
  })
  if (completa) {
    return { tarefas: fase.tarefas, total: fase.total_tarefas, carregando: false, temMais: false, carregarMais: () => {}, carregandoMais: false, erro: null }
  }
  return {
    tarefas: consulta.data?.pages.flatMap((p) => p.itens) ?? [],
    total: consulta.data?.pages[0]?.total ?? 0,
    carregando: consulta.isPending,
    temMais: !!consulta.hasNextPage,
    carregarMais: () => void consulta.fetchNextPage(),
    carregandoMais: consulta.isFetchingNextPage,
    erro: consulta.error,
  }
}

interface PropsFase {
  projetoId: number
  fase: Fase
  indice: number
  filtroPilar: string
  indicadores?: Indicadores
  podeEditar: boolean
  nomes: Map<number, string>
  nomesPilares: Map<number, string>
  dependencias: MapaDependencias
  aoCriar: () => void
  aoEditar: (t: Tarefa) => void
  aoExcluir: (t: Tarefa) => void
  aoComentar: (t: Tarefa) => void
}

function CartaoFase({ projetoId, fase, indice, filtroPilar, indicadores, podeEditar, nomes, nomesPilares, dependencias, aoCriar, aoEditar, aoExcluir, aoComentar }: PropsFase) {
  const ind = indicadores?.fases.find((f) => f.fase_id === fase.id)
  const lista = useTarefasDaFase(projetoId, fase, filtroPilar)
  const tarefas = lista.tarefas
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b py-4">
        <CardTitle className="flex items-center gap-2 text-base">
          <span className={cn("size-3 rounded-full", corFase(indice))} />
          {fase.ordem_sequencia}. {fase.titulo}
          {indicadores?.fase_atual === fase.titulo && <Badge variant="secondary">Fase atual</Badge>}
        </CardTitle>
        <CardDescription className="flex items-center gap-4">
          <span>{fmt.pluralizar(fase.total_tarefas, "tarefa", "tarefas")}</span>
          {ind && ind.total_tarefas > 0 && <BarraProgresso valor={ind.percentual_conclusao} className="w-40" />}
          {ind && ind.tarefas_atrasadas > 0 && (
            <span className="text-destructive">{fmt.pluralizar(ind.tarefas_atrasadas, "atrasada", "atrasadas")}</span>
          )}
        </CardDescription>
        {podeEditar && (
          <CardAction>
            <Button size="sm" variant="outline" onClick={aoCriar} aria-label={`Nova tarefa em ${fase.titulo}`}>
              <Plus /> Tarefa
            </Button>
          </CardAction>
        )}
      </CardHeader>
      {lista.carregando ? (
        <p className="px-6 py-4 text-sm text-muted-foreground">Carregando tarefas...</p>
      ) : tarefas.length === 0 ? (
        <p className="px-6 py-4 text-sm text-muted-foreground">
          {fase.total_tarefas === 0 ? "Nenhuma tarefa nesta fase." : "Nenhuma tarefa deste pilar nesta fase."}
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-6">Tarefa</TableHead>
              <TableHead>Responsável</TableHead>
              <TableHead>Início</TableHead>
              <TableHead>Término</TableHead>
              <TableHead className="w-44">Andamento</TableHead>
              <TableHead className="w-28 pr-6 text-right">
                <span className="sr-only">Ações</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tarefas.map((t) => {
              const vencida = fmt.atrasada(t.data_fim, t.percentual_conclusao)
              return (
                <TableRow key={t.id}>
                  <TableCell className="max-w-sm pl-6 whitespace-normal">
                    {t.descricao}
                    {t.pilar_id !== null && nomesPilares.has(t.pilar_id) && (
                      <Badge variant="outline" className="ml-2 align-middle font-normal">
                        {nomesPilares.get(t.pilar_id)}
                      </Badge>
                    )}
                    <SeloDependencia mapa={dependencias} tarefa={t} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {(t.responsavel_id && nomes.get(t.responsavel_id)) || t.responsavel || "—"}
                  </TableCell>
                  <TableCell className="tabular-nums">{fmt.data(t.data_inicio)}</TableCell>
                  <TableCell className={cn("tabular-nums", vencida && "font-medium text-destructive")}>
                    {fmt.data(t.data_fim)}
                    {vencida && <span className="sr-only"> (atrasada)</span>}
                  </TableCell>
                  <TableCell>
                    <BarraProgresso valor={t.percentual_conclusao} />
                  </TableCell>
                  <TableCell className="pr-6">
                    <div className="flex justify-end gap-1">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button variant="ghost" size="icon-sm" aria-label="Comentários da tarefa" onClick={() => aoComentar(t)}>
                            <MessageSquare />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Comentários</TooltipContent>
                      </Tooltip>
                      {podeEditar && (
                        <>
                          <Button variant="ghost" size="icon-sm" aria-label="Editar tarefa" onClick={() => aoEditar(t)}>
                            <Pencil />
                          </Button>
                          <Button variant="ghost" size="icon-sm" aria-label="Excluir tarefa" onClick={() => aoExcluir(t)}>
                            <Trash2 />
                          </Button>
                        </>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
      {lista.erro && (
        <p role="alert" className="border-t px-6 py-3 text-sm text-destructive">
          Não foi possível carregar as tarefas: {lista.erro.message}
        </p>
      )}
      {lista.temMais && (
        <div className="flex items-center justify-between border-t px-6 py-3 text-sm text-muted-foreground">
          <span>
            Mostrando {tarefas.length.toLocaleString("pt-BR")} de {lista.total.toLocaleString("pt-BR")} tarefas
          </span>
          <Button size="sm" variant="outline" onClick={lista.carregarMais} disabled={lista.carregandoMais}>
            {lista.carregandoMais ? "Carregando..." : `Carregar mais ${Math.min(POR_PAGINA, lista.total - tarefas.length).toLocaleString("pt-BR")}`}
          </Button>
        </div>
      )}
    </Card>
  )
}
