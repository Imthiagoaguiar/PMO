import { useMutation } from "@tanstack/react-query"
import { MessageSquare, Pencil, Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { BarraProgresso, COR_FASE } from "@/components/comum"
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
import type { Fase, Indicadores, Membro, Projeto, Tarefa } from "@/lib/tipos"

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
  const visivel = (t: Tarefa) =>
    filtroPilar === TODOS || (filtroPilar === SEM_PILAR ? t.pilar_id === null : t.pilar_id === Number(filtroPilar))

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
      {projeto.pilares.length > 0 && (
        <div className="flex items-center gap-2 text-sm">
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
      {projeto.fases.map((fase, i) => {
        const ind = indicadores?.fases.find((f) => f.fase_id === fase.id)
        const tarefas = fase.tarefas.filter(visivel)
        return (
          <Card key={fase.id} className="gap-0 py-0">
            <CardHeader className="border-b py-4">
              <CardTitle className="flex items-center gap-2 text-base">
                <span className={cn("size-3 rounded-full", COR_FASE[i])} />
                {fase.ordem_sequencia}. {fase.titulo}
                {indicadores?.fase_atual === fase.titulo && <Badge variant="secondary">Fase atual</Badge>}
              </CardTitle>
              <CardDescription className="flex items-center gap-4">
                <span>{fmt.pluralizar(fase.tarefas.length, "tarefa", "tarefas")}</span>
                {ind && ind.total_tarefas > 0 && <BarraProgresso valor={ind.percentual_conclusao} className="w-40" />}
                {ind && ind.tarefas_atrasadas > 0 && (
                  <span className="text-destructive">{fmt.pluralizar(ind.tarefas_atrasadas, "atrasada", "atrasadas")}</span>
                )}
              </CardDescription>
              {podeEditar && (
                <CardAction>
                  <Button size="sm" variant="outline" onClick={() => setEditando({ fase })}>
                    <Plus /> Tarefa
                  </Button>
                </CardAction>
              )}
            </CardHeader>
            {tarefas.length === 0 ? (
              <p className="px-6 py-4 text-sm text-muted-foreground">
                {fase.tarefas.length === 0 ? "Nenhuma tarefa nesta fase." : "Nenhuma tarefa deste pilar nesta fase."}
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
                                <Button variant="ghost" size="icon-sm" aria-label="Comentários da tarefa" onClick={() => setComentando(t)}>
                                  <MessageSquare />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Comentários</TooltipContent>
                            </Tooltip>
                            {podeEditar && (
                              <>
                                <Button variant="ghost" size="icon-sm" aria-label="Editar tarefa" onClick={() => setEditando({ fase, tarefa: t })}>
                                  <Pencil />
                                </Button>
                                <Button variant="ghost" size="icon-sm" aria-label="Excluir tarefa" onClick={() => setExcluindo(t)}>
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
          </Card>
        )
      })}

      {editando && (
        <TarefaDialog
          projetoId={projeto.id}
          fases={projeto.fases}
          membros={membros}
          pilares={projeto.pilares}
          faseInicial={editando.fase}
          tarefa={editando.tarefa}
          aoFechar={() => setEditando(null)}
        />
      )}

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
