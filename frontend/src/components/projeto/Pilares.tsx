import { useMutation } from "@tanstack/react-query"
import { Pencil, Plus, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { BarraProgresso, Campo, Carregando, ErroCarregar, Vazio } from "@/components/comum"
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
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto, usePilares } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { FarolPilar, Pilar } from "@/lib/tipos"

const MAX_PILARES = 20

export const FAROL: Record<FarolPilar, { rotulo: string; ponto: string; borda: string }> = {
  verde: { rotulo: "Verde", ponto: "bg-emerald-500", borda: "border-l-emerald-500" },
  amarelo: { rotulo: "Amarelo", ponto: "bg-amber-400", borda: "border-l-amber-400" },
  vermelho: { rotulo: "Vermelho", ponto: "bg-red-500", borda: "border-l-red-500" },
}
const SEM_FAROL = "sem"

export function Farol({ farol, className }: { farol: FarolPilar | null; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs text-muted-foreground", className)}>
      <span className={cn("size-2.5 rounded-full", farol ? FAROL[farol].ponto : "bg-muted-foreground/30")} />
      {farol ? FAROL[farol].rotulo : "Sem farol"}
    </span>
  )
}

export function Pilares({ projetoId, podeEditar }: { projetoId: number; podeEditar: boolean }) {
  const consulta = usePilares(projetoId)
  const [editando, setEditando] = useState<Pilar | "novo" | null>(null)
  const [excluindo, setExcluindo] = useState<Pilar | null>(null)
  const atualizar = useAtualizarProjeto(projetoId)

  const excluir = useMutation({
    mutationFn: (p: Pilar) => api(`/projetos/${projetoId}/pilares/${p.id}`, { method: "DELETE" }),
    onSuccess: () => {
      atualizar()
      toast.success("Pilar excluído. As tarefas dele continuam no cronograma.")
      setExcluindo(null)
    },
    onError: fmt.notificarErro,
  })

  if (consulta.isPending) return <Carregando />
  if (consulta.isError) return <ErroCarregar erro={consulta.error} />
  const pilares = consulta.data

  const vermelhos = pilares.filter((p) => p.farol === "vermelho").length
  const orcado = pilares.reduce((soma, p) => soma + Number(p.valor_orcado ?? 0), 0)
  const realizado = pilares.reduce((soma, p) => soma + Number(p.valor_realizado ?? 0), 0)

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Cada tarefa pode pertencer a um pilar: o andamento vem das tarefas; o farol e a situação, de quem acompanha a frente.
          {vermelhos > 0 && <span className="ml-1 font-medium text-destructive">{fmt.pluralizar(vermelhos, "pilar em vermelho", "pilares em vermelho")}.</span>}
          {orcado > 0 && <span className="ml-1">Financeiro: {fmt.moeda(realizado)} de {fmt.moeda(orcado)} orçados.</span>}
        </p>
        {podeEditar && (
          <Button size="sm" variant="outline" onClick={() => setEditando("novo")} disabled={pilares.length >= MAX_PILARES}>
            <Plus /> Pilar
          </Button>
        )}
      </div>

      {pilares.length === 0 ? (
        <Vazio titulo="Nenhum pilar">Crie pilares como TI, Suprimentos, CAPEX ou Pessoas para acompanhar cada frente.</Vazio>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {pilares.map((p) => (
            <CartaoPilar key={p.id} pilar={p} podeEditar={podeEditar} aoEditar={() => setEditando(p)} aoExcluir={() => setExcluindo(p)} />
          ))}
        </div>
      )}

      {editando && (
        <PilarDialog projetoId={projetoId} pilar={editando === "novo" ? undefined : editando} aoFechar={() => setEditando(null)} />
      )}

      <AlertDialog open={!!excluindo} onOpenChange={(aberto) => !aberto && setExcluindo(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir o pilar “{excluindo?.nome}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {excluindo && excluindo.total_tarefas > 0
                ? `As ${excluindo.total_tarefas} tarefas dele continuam no cronograma, só ficam sem pilar.`
                : "Nenhuma tarefa está neste pilar."}{" "}
              O farol, a situação e os valores do pilar são perdidos.
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
              disabled={excluir.isPending}
            >
              Excluir
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function CartaoPilar({ pilar: p, podeEditar, aoEditar, aoExcluir }: {
  pilar: Pilar
  podeEditar: boolean
  aoEditar: () => void
  aoExcluir: () => void
}) {
  const desvio = p.desvio === null ? null : Number(p.desvio)
  return (
    <Card className={cn("gap-3 border-l-4 py-4", p.farol ? FAROL[p.farol].borda : "border-l-muted")}>
      <CardHeader className="px-5">
        <CardTitle className="flex items-center gap-3 text-base">
          {p.nome}
          <Farol farol={p.farol} />
        </CardTitle>
        {podeEditar && (
          <CardAction className="flex gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Editar pilar ${p.nome}`} onClick={aoEditar}>
                  <Pencil />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Editar farol, situação e valores</TooltipContent>
            </Tooltip>
            <Button variant="ghost" size="icon-sm" aria-label={`Excluir pilar ${p.nome}`} onClick={aoExcluir}>
              <Trash2 />
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="grid gap-3 px-5 text-sm">
        <div>
          <BarraProgresso valor={p.percentual_conclusao} />
          <p className="mt-1 text-xs text-muted-foreground">
            {p.total_tarefas === 0
              ? "Nenhuma tarefa ligada a este pilar."
              : `${p.tarefas_concluidas} de ${fmt.pluralizar(p.total_tarefas, "tarefa concluída", "tarefas concluídas")}`}
            {p.tarefas_atrasadas > 0 && (
              <span className="text-destructive"> · {fmt.pluralizar(p.tarefas_atrasadas, "atrasada", "atrasadas")}</span>
            )}
          </p>
        </div>

        {(p.valor_orcado !== null || p.valor_realizado !== null) && (
          <div className="grid grid-cols-3 gap-2 rounded-md bg-muted/50 p-2.5 text-xs">
            <div>
              <p className="text-muted-foreground">Orçado</p>
              <p className="font-medium tabular-nums">{fmt.moeda(p.valor_orcado)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Realizado</p>
              <p className="font-medium tabular-nums">
                {fmt.moeda(p.valor_realizado)}
                {p.execucao_financeira !== null && <span className="ml-1 text-muted-foreground">({Math.round(p.execucao_financeira)}%)</span>}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Desvio</p>
              <p className={cn("font-medium tabular-nums", desvio !== null && desvio > 0 && "text-destructive")}>
                {desvio === null ? "—" : `${desvio > 0 ? "+" : ""}${fmt.moeda(desvio)}`}
              </p>
            </div>
          </div>
        )}

        {p.status_texto ? (
          <div>
            <p className="whitespace-pre-line">{p.status_texto}</p>
            {p.status_atualizado_em && (
              <p className="mt-1 text-xs text-muted-foreground">
                Atualizado em {fmt.dataHora(p.status_atualizado_em)}
                {p.status_atualizado_por && ` por ${p.status_atualizado_por.nome}`}
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Sem situação registrada.</p>
        )}
      </CardContent>
    </Card>
  )
}

function PilarDialog({ projetoId, pilar, aoFechar }: { projetoId: number; pilar?: Pilar; aoFechar: () => void }) {
  const atualizar = useAtualizarProjeto(projetoId)
  const [nome, setNome] = useState(pilar?.nome ?? "")
  const [farol, setFarol] = useState<string>(pilar?.farol ?? SEM_FAROL)
  const [situacao, setSituacao] = useState(pilar?.status_texto ?? "")
  const [orcado, setOrcado] = useState(pilar?.valor_orcado ?? "")
  const [realizado, setRealizado] = useState(pilar?.valor_realizado ?? "")

  const dinheiro = (v: string) => (v.trim() === "" ? null : v.trim().replace(",", "."))

  const salvar = useMutation({
    mutationFn: () => {
      if (!pilar) {
        return api<Pilar>(`/projetos/${projetoId}/pilares`, { method: "POST", json: { nome: nome.trim(), valor_orcado: dinheiro(orcado) } })
      }
      const dados: Record<string, unknown> = {
        nome: nome.trim(),
        farol: farol === SEM_FAROL ? null : farol,
        status_texto: situacao.trim() || null,
        valor_orcado: dinheiro(orcado),
        valor_realizado: dinheiro(realizado),
      }
      // Só o que mudou (o histórico e o "atualizado por" ficam corretos).
      const original: Record<string, unknown> = { ...pilar }
      for (const campo of Object.keys(dados)) {
        const antes = original[campo]
        const igual = campo.startsWith("valor_")
          ? (antes === null ? dados[campo] === null : dados[campo] !== null && Number(antes) === Number(dados[campo]))
          : antes === dados[campo]
        if (igual) delete dados[campo]
      }
      return api<Pilar>(`/projetos/${projetoId}/pilares/${pilar.id}`, { method: "PATCH", json: { ...dados, versao: pilar.versao } })
    },
    onSuccess: () => {
      atualizar()
      toast.success(pilar ? "Pilar atualizado." : "Pilar criado.")
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

  function enviar(e: FormEvent) {
    e.preventDefault()
    salvar.mutate()
  }

  return (
    <Dialog open onOpenChange={(aberto) => !aberto && !salvar.isPending && aoFechar()}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{pilar ? `Pilar ${pilar.nome}` : "Novo pilar"}</DialogTitle>
            <DialogDescription>
              {pilar
                ? "Atualize o farol e a situação sempre que acompanhar esta frente."
                : "Uma frente de trabalho do projeto, como Jurídico ou Marketing."}
            </DialogDescription>
          </DialogHeader>

          <Campo label="Nome" htmlFor="pilar-nome">
            <Input id="pilar-nome" value={nome} maxLength={60} onChange={(e) => setNome(e.target.value)} required autoFocus={!pilar} />
          </Campo>

          {pilar && (
            <>
              <Campo label="Farol">
                <Select value={farol} onValueChange={setFarol}>
                  <SelectTrigger className="w-full" aria-label="Farol">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={SEM_FAROL}>Sem avaliação</SelectItem>
                    {(Object.keys(FAROL) as FarolPilar[]).map((f) => (
                      <SelectItem key={f} value={f}>
                        <Farol farol={f} className="text-foreground" />
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Campo>
              <Campo label="Situação" htmlFor="pilar-situacao" dica="O que está acontecendo nesta frente, riscos e próximos passos.">
                <Textarea id="pilar-situacao" rows={4} maxLength={1000} value={situacao} onChange={(e) => setSituacao(e.target.value)} />
              </Campo>
            </>
          )}

          <div className="grid grid-cols-2 gap-4">
            <Campo label="Orçado (R$)" htmlFor="pilar-orcado">
              <Input id="pilar-orcado" type="number" min="0" step="0.01" inputMode="decimal" value={orcado} onChange={(e) => setOrcado(e.target.value)} />
            </Campo>
            {pilar && (
              <Campo label="Realizado (R$)" htmlFor="pilar-realizado">
                <Input id="pilar-realizado" type="number" min="0" step="0.01" inputMode="decimal" value={realizado} onChange={(e) => setRealizado(e.target.value)} />
              </Campo>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar} disabled={salvar.isPending}>
              Cancelar
            </Button>
            <Button type="submit" disabled={salvar.isPending || !nome.trim()}>
              {salvar.isPending ? "Salvando..." : "Salvar"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
