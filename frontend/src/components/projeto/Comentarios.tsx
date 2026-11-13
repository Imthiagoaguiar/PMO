import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Pencil, Send, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"

import { Carregando, ErroCarregar, Vazio } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { useUsuario } from "@/lib/auth"
import { chaves } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import type { Comentario, Pagina, Projeto, Tarefa } from "@/lib/tipos"

const LIMITE = 20
const MAX = 2000

interface Props {
  projeto: Projeto
  /** Só os comentários desta tarefa (e novos comentários vão para ela). */
  tarefa?: Tarefa
}

function Item({ comentario, projeto, nomeTarefa }: { comentario: Comentario; projeto: Projeto; nomeTarefa?: string }) {
  const usuario = useUsuario()
  const queryClient = useQueryClient()
  const [editando, setEditando] = useState(false)
  const [texto, setTexto] = useState(comentario.texto)
  const autor = comentario.autor?.id === usuario.id
  const podeExcluir = autor || !!projeto.permissoes?.gerenciar
  const url = `/projetos/${projeto.id}/comentarios/${comentario.id}`
  const recarregar = () => queryClient.invalidateQueries({ queryKey: ["projeto", projeto.id] })

  const salvar = useMutation({
    mutationFn: () => api<Comentario>(url, { method: "PATCH", json: { texto } }),
    onSuccess: () => {
      setEditando(false)
      recarregar()
    },
    onError: fmt.notificarErro,
  })
  const excluir = useMutation({
    mutationFn: () => api(url, { method: "DELETE" }),
    onSuccess: recarregar,
    onError: fmt.notificarErro,
  })

  return (
    <li className="group flex gap-3">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-semibold uppercase">
        {comentario.autor?.nome.slice(0, 2) ?? "?"}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-medium">{comentario.autor?.nome ?? "Usuário removido"}</span>
          <span className="text-xs text-muted-foreground">
            {fmt.dataHora(comentario.criado_em)}
            {comentario.editado_em && " · editado"}
          </span>
          {nomeTarefa && <span className="truncate text-xs text-primary">em “{nomeTarefa}”</span>}
          <span className="ml-auto flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            {autor && !editando && (
              <Button variant="ghost" size="icon-xs" aria-label="Editar comentário" onClick={() => setEditando(true)}>
                <Pencil />
              </Button>
            )}
            {podeExcluir && (
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Excluir comentário"
                disabled={excluir.isPending}
                onClick={() => window.confirm("Excluir este comentário?") && excluir.mutate()}
              >
                <Trash2 />
              </Button>
            )}
          </span>
        </div>
        {editando ? (
          <div className="mt-1 grid gap-2">
            <Textarea rows={3} maxLength={MAX} value={texto} onChange={(e) => setTexto(e.target.value)} />
            <div className="flex gap-2">
              <Button size="sm" disabled={!texto.trim() || salvar.isPending} onClick={() => salvar.mutate()}>
                Salvar
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setTexto(comentario.texto)
                  setEditando(false)
                }}
              >
                Cancelar
              </Button>
            </div>
          </div>
        ) : (
          // Texto puro (o React escapa): sem risco de HTML injetado no comentário.
          <p className="mt-0.5 text-sm break-words whitespace-pre-wrap">{comentario.texto}</p>
        )}
      </div>
    </li>
  )
}

export function Comentarios({ projeto, tarefa }: Props) {
  const queryClient = useQueryClient()
  const [texto, setTexto] = useState("")
  const filtro = tarefa ? `tarefa_id=${tarefa.id}` : ""
  const tarefasPorId = new Map(projeto.fases.flatMap((f) => f.tarefas).map((t) => [t.id, t.descricao]))

  const consulta = useInfiniteQuery({
    queryKey: chaves.comentarios(projeto.id, filtro),
    queryFn: ({ pageParam }) =>
      api<Pagina<Comentario>>(`/projetos/${projeto.id}/comentarios?limite=${LIMITE}&offset=${pageParam}&${filtro}`),
    initialPageParam: 0,
    getNextPageParam: (ultima) => (ultima.offset + ultima.limite < ultima.total ? ultima.offset + ultima.limite : undefined),
  })

  const enviar = useMutation({
    mutationFn: () =>
      api<Comentario>(`/projetos/${projeto.id}/comentarios`, {
        method: "POST",
        json: { texto, tarefa_id: tarefa?.id ?? null },
      }),
    onSuccess: () => {
      setTexto("")
      queryClient.invalidateQueries({ queryKey: ["projeto", projeto.id] })
    },
    onError: fmt.notificarErro,
  })

  function submeter(e: FormEvent) {
    e.preventDefault()
    if (texto.trim()) enviar.mutate()
  }

  const comentarios = consulta.data?.pages.flatMap((p) => p.itens) ?? []

  return (
    <div className="grid gap-6">
      <form onSubmit={submeter} className="grid gap-2">
        <Textarea
          rows={3}
          maxLength={MAX}
          placeholder={tarefa ? "Comente sobre esta tarefa..." : "Escreva um comentário sobre o projeto..."}
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submeter(e)
          }}
        />
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{texto.length > MAX - 200 ? `${texto.length}/${MAX}` : "Ctrl/⌘ + Enter para enviar"}</span>
          <Button type="submit" size="sm" disabled={!texto.trim() || enviar.isPending}>
            <Send /> Comentar
          </Button>
        </div>
      </form>

      {consulta.isPending ? (
        <Carregando />
      ) : consulta.isError ? (
        <ErroCarregar erro={consulta.error} />
      ) : comentarios.length === 0 ? (
        <Vazio titulo="Nenhum comentário ainda" />
      ) : (
        <ul className="grid gap-5">
          {comentarios.map((c) => (
            <Item
              key={c.id}
              comentario={c}
              projeto={projeto}
              nomeTarefa={!tarefa && c.tarefa_id ? tarefasPorId.get(c.tarefa_id) : undefined}
            />
          ))}
        </ul>
      )}
      {consulta.hasNextPage && (
        <Button variant="outline" className="justify-self-center" disabled={consulta.isFetchingNextPage} onClick={() => consulta.fetchNextPage()}>
          Carregar mais
        </Button>
      )}
    </div>
  )
}

export function ComentariosDialog({ projeto, tarefa, aoFechar }: { projeto: Projeto; tarefa: Tarefa; aoFechar: () => void }) {
  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Comentários</DialogTitle>
          <DialogDescription className="line-clamp-2">{tarefa.descricao}</DialogDescription>
        </DialogHeader>
        <Comentarios projeto={projeto} tarefa={tarefa} />
      </DialogContent>
    </Dialog>
  )
}
