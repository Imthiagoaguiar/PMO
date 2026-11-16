import { useMutation } from "@tanstack/react-query"
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { corFase } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { Fase, Projeto } from "@/lib/tipos"

const MAX_FASES = 20
const EXCLUIR_JUNTO = "excluir"

/** Renomear, reordenar, excluir e criar fases; e escolher se o projeto segue a ordem delas. */
export function GerenciarFases({ projeto, aoFechar }: { projeto: Projeto; aoFechar: () => void }) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const base = `/projetos/${projeto.id}/fases`
  const [nova, setNova] = useState("")
  const [excluindo, setExcluindo] = useState<Fase | null>(null)

  const ok = (mensagem: string) => () => {
    atualizar()
    toast.success(mensagem)
  }

  const criar = useMutation({
    mutationFn: (titulo: string) => api<Fase>(base, { method: "POST", json: { titulo } }),
    onSuccess: (f) => {
      setNova("")
      ok(`Fase “${f.titulo}” criada.`)()
    },
    onError: fmt.notificarErro,
  })
  const renomear = useMutation({
    mutationFn: ({ fase, titulo }: { fase: Fase; titulo: string }) =>
      api<Fase>(`${base}/${fase.id}`, { method: "PATCH", json: { titulo } }),
    onSuccess: ok("Fase renomeada."),
    onError: (erro) => {
      fmt.notificarErro(erro)
      atualizar() // volta o nome que está salvo
    },
  })
  const reordenar = useMutation({
    mutationFn: (ids: number[]) => api(`${base}/ordem`, { method: "PUT", json: { ids } }),
    onSuccess: atualizar,
    onError: (erro) => {
      fmt.notificarErro(erro)
      atualizar()
    },
  })
  const seguirOrdem = useMutation({
    mutationFn: (seguir: boolean) =>
      api(`/projetos/${projeto.id}`, { method: "PATCH", json: { seguir_ordem_fases: seguir, versao: projeto.versao } }),
    onSuccess: (_, seguir) => ok(seguir ? "O projeto segue a ordem das fases." : "As fases agora andam em paralelo.")(),
    onError: (erro) => {
      fmt.notificarErro(erro)
      if (ehConflitoDeVersao(erro)) atualizar()
    },
  })

  function mover(indice: number, delta: number) {
    const ids = projeto.fases.map((f) => f.id)
    const alvo = indice + delta
    if (alvo < 0 || alvo >= ids.length) return
    ;[ids[indice], ids[alvo]] = [ids[alvo], ids[indice]]
    reordenar.mutate(ids)
  }

  function adicionar(e: FormEvent) {
    e.preventDefault()
    if (nova.trim()) criar.mutate(nova.trim())
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Fases do projeto</DialogTitle>
          <DialogDescription>
            O Waterfall é só o ponto de partida: renomeie, mude a ordem, exclua ou crie as fases que fizerem sentido.
          </DialogDescription>
        </DialogHeader>

        <label className="flex cursor-pointer items-start gap-2 rounded-md border p-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={projeto.seguir_ordem_fases}
            disabled={seguirOrdem.isPending}
            onChange={(e) => seguirOrdem.mutate(e.target.checked)}
          />
          <span>
            <span className="font-medium">Seguir a ordem das fases</span>
            <span className="block text-muted-foreground">
              Ligado: a “fase atual” é a primeira, na ordem, com tarefa pendente. Desligado: as fases andam em paralelo.
            </span>
          </span>
        </label>

        {projeto.fases.length === 0 ? (
          <p className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
            Nenhuma fase ainda. Crie a primeira abaixo.
          </p>
        ) : (
          <ol className="grid gap-2" aria-label="Fases">
            {projeto.fases.map((fase, i) => (
              <li key={fase.id} className="flex items-center gap-2">
                <span className={cn("size-3 shrink-0 rounded-full", corFase(i))} />
                <span className="w-5 text-right text-sm text-muted-foreground tabular-nums">{i + 1}.</span>
                <NomeDaFase fase={fase} aoSalvar={(titulo) => renomear.mutate({ fase, titulo })} />
                <span className="w-16 shrink-0 text-right text-xs text-muted-foreground">
                  {fmt.pluralizar(fase.total_tarefas, "tarefa", "tarefas")}
                </span>
                <Button
                  type="button" variant="ghost" size="icon-sm" aria-label={`Subir ${fase.titulo}`}
                  disabled={i === 0 || reordenar.isPending} onClick={() => mover(i, -1)}
                >
                  <ArrowUp />
                </Button>
                <Button
                  type="button" variant="ghost" size="icon-sm" aria-label={`Descer ${fase.titulo}`}
                  disabled={i === projeto.fases.length - 1 || reordenar.isPending} onClick={() => mover(i, 1)}
                >
                  <ArrowDown />
                </Button>
                <Button type="button" variant="ghost" size="icon-sm" aria-label={`Excluir ${fase.titulo}`} onClick={() => setExcluindo(fase)}>
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ol>
        )}

        <form onSubmit={adicionar} className="flex gap-2">
          <Input
            aria-label="Nome da nova fase"
            placeholder="Nova fase (ex.: Homologação)"
            maxLength={50}
            value={nova}
            onChange={(e) => setNova(e.target.value)}
            disabled={projeto.fases.length >= MAX_FASES}
          />
          <Button type="submit" variant="outline" disabled={!nova.trim() || criar.isPending || projeto.fases.length >= MAX_FASES}>
            <Plus /> Adicionar
          </Button>
        </form>
        {projeto.fases.length >= MAX_FASES && (
          <p className="text-xs text-muted-foreground">Limite de {MAX_FASES} fases por projeto.</p>
        )}

        {excluindo && (
          <ExcluirFase
            projeto={projeto}
            fase={excluindo}
            aoFechar={() => setExcluindo(null)}
            aoExcluir={ok(`Fase “${excluindo.titulo}” excluída.`)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Campo do nome: salva ao sair do campo ou com Enter (Esc desfaz). */
function NomeDaFase({ fase, aoSalvar }: { fase: Fase; aoSalvar: (titulo: string) => void }) {
  const [titulo, setTitulo] = useState(fase.titulo)
  const [salvo, setSalvo] = useState(fase.titulo)
  if (fase.titulo !== salvo) {
    // O servidor mudou o nome (outra pessoa, ou um erro desfez): acompanha.
    setSalvo(fase.titulo)
    setTitulo(fase.titulo)
  }
  function salvar() {
    const limpo = titulo.trim()
    if (!limpo) setTitulo(fase.titulo)
    else if (limpo !== fase.titulo) aoSalvar(limpo)
  }
  return (
    <Input
      aria-label={`Nome da fase ${fase.ordem_sequencia}`}
      className="h-8"
      maxLength={50}
      value={titulo}
      onChange={(e) => setTitulo(e.target.value)}
      onBlur={salvar}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault()
          e.currentTarget.blur()
        } else if (e.key === "Escape") {
          e.stopPropagation()
          setTitulo(fase.titulo)
        }
      }}
    />
  )
}

function ExcluirFase({ projeto, fase, aoFechar, aoExcluir }: {
  projeto: Projeto
  fase: Fase
  aoFechar: () => void
  aoExcluir: () => void
}) {
  const outras = projeto.fases.filter((f) => f.id !== fase.id)
  const temTarefas = fase.total_tarefas > 0
  const [destino, setDestino] = useState(outras[0] ? String(outras[0].id) : EXCLUIR_JUNTO)
  const excluir = useMutation({
    mutationFn: () => {
      const params = new URLSearchParams()
      if (temTarefas) {
        if (destino === EXCLUIR_JUNTO) params.set("excluir_tarefas", "true")
        else params.set("mover_para", destino)
      }
      return api(`/projetos/${projeto.id}/fases/${fase.id}?${params}`, { method: "DELETE" })
    },
    onSuccess: () => {
      aoExcluir()
      aoFechar()
    },
    onError: fmt.notificarErro,
  })

  return (
    <div role="alertdialog" aria-label={`Excluir a fase ${fase.titulo}`} className="grid gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
      <p className="font-medium">Excluir a fase “{fase.titulo}”?</p>
      {temTarefas ? (
        <>
          <p className="text-muted-foreground">
            Ela tem {fmt.pluralizar(fase.total_tarefas, "tarefa", "tarefas")}. Escolha o que fazer com elas:
          </p>
          <Select value={destino} onValueChange={setDestino}>
            <SelectTrigger aria-label="Destino das tarefas" className="bg-background">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {outras.map((f) => (
                <SelectItem key={f.id} value={String(f.id)}>
                  Mover para “{f.titulo}”
                </SelectItem>
              ))}
              <SelectItem value={EXCLUIR_JUNTO}>Excluir junto com as tarefas</SelectItem>
            </SelectContent>
          </Select>
        </>
      ) : (
        <p className="text-muted-foreground">A fase está vazia.</p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={aoFechar}>
          Cancelar
        </Button>
        <Button
          type="button" size="sm" className="bg-destructive text-white hover:bg-destructive/90"
          disabled={excluir.isPending} onClick={() => excluir.mutate()}
        >
          {excluir.isPending
            ? "Excluindo..."
            : temTarefas && destino === EXCLUIR_JUNTO ? "Excluir fase e tarefas" : "Excluir fase"}
        </Button>
      </div>
    </div>
  )
}
