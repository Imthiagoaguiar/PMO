import { useMutation } from "@tanstack/react-query"
import { CheckCircle2, Lock, Sparkles, TriangleAlert } from "lucide-react"
import { useState } from "react"

import { Carregando, ErroCarregar, Vazio } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { api } from "@/lib/api"
import { useFeedback } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { FeedbackEntidade } from "@/lib/tipos"

type Visao = "pessoas" | "areas"

function Numero({ rotulo, valor, ruim }: { rotulo: string; valor: string; ruim?: boolean }) {
  return (
    <div className="rounded-md bg-muted/50 px-2.5 py-1.5">
      <p className="text-[11px] text-muted-foreground">{rotulo}</p>
      <p className={cn("text-sm font-semibold tabular-nums", ruim && "text-destructive")}>{valor}</p>
    </div>
  )
}

function CartaoFeedback({ projetoId, entidade, tipo }: { projetoId: number; entidade: FeedbackEntidade; tipo: "pessoa" | "area" }) {
  const n = entidade.numeros
  const textoIa = useMutation({
    mutationFn: () => api<{ texto: string; modelo: string }>(`/projetos/${projetoId}/feedback/ia`, {
      method: "POST", json: { tipo, chave: entidade.chave },
    }),
    onError: fmt.notificarErro,
  })
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle className="text-base">{entidade.nome}</CardTitle>
        <CardDescription>{fmt.pluralizar(n.tarefas, "tarefa", "tarefas")} · {n.entregues} entregue(s) · {n.abertas} em aberto</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid grid-cols-3 gap-2">
          <Numero rotulo="Pontualidade" valor={n.pontualidade === null ? "—" : `${n.pontualidade}%`} ruim={n.pontualidade !== null && n.pontualidade < 70} />
          <Numero rotulo="Atrasadas agora" valor={String(n.abertas_vencidas)} ruim={n.abertas_vencidas > 0} />
          <Numero rotulo="Retrabalho" valor={String(n.reaberturas)} ruim={n.reaberturas >= 2} />
        </div>
        {entidade.pontos_fortes.length > 0 && (
          <div>
            <p className="mb-1.5 text-sm font-medium">Pontos fortes</p>
            <ul className="grid gap-1.5 text-sm">
              {entidade.pontos_fortes.map((f) => (
                <li key={f} className="flex gap-2"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden />{f}</li>
              ))}
            </ul>
          </div>
        )}
        {entidade.pontos_a_melhorar.length > 0 && (
          <div>
            <p className="mb-1.5 text-sm font-medium">Pontos a melhorar</p>
            <ul className="grid gap-1.5 text-sm">
              {entidade.pontos_a_melhorar.map((f) => (
                <li key={f} className="flex gap-2"><TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />{f}</li>
              ))}
            </ul>
          </div>
        )}
        {entidade.observacao && <p className="text-sm text-muted-foreground">{entidade.observacao}</p>}
        {textoIa.data ? (
          <div className="rounded-md border bg-muted/30 p-3 text-sm">
            <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted-foreground"><Sparkles className="size-3.5" /> Sugestão de texto (IA)</p>
            <p className="whitespace-pre-line">{textoIa.data.texto}</p>
          </div>
        ) : (
          (entidade.pontos_fortes.length > 0 || entidade.pontos_a_melhorar.length > 0) && (
            <Button variant="outline" size="sm" className="justify-self-start" carregando={textoIa.isPending} onClick={() => textoIa.mutate()}>
              <Sparkles /> {textoIa.isPending ? "Escrevendo..." : "Sugerir texto com IA"}
            </Button>
          )
        )}
      </CardContent>
    </Card>
  )
}

export function Feedback({ projetoId }: { projetoId: number }) {
  const consulta = useFeedback(projetoId, true)
  const [visao, setVisao] = useState<Visao>("pessoas")
  if (consulta.isPending) return <Carregando texto="Calculando o feedback..." />
  if (consulta.isError) return <ErroCarregar erro={consulta.error} />
  const f = consulta.data
  const lista = f[visao]
  return (
    <div className="grid gap-4">
      <div className="flex items-start gap-2 rounded-lg border bg-muted/40 px-4 py-3 text-sm">
        <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        <p>
          <span className="font-medium">Visível só para o dono do projeto e administradores.</span> Os pontos saem dos números do
          cronograma (prazos, entregas, retrabalho) e comparam com a média do projeto
          {f.pontualidade_projeto !== null && ` (pontualidade média: ${f.pontualidade_projeto}%)`}. Use como ponto de partida
          para uma conversa, não como avaliação final: números não contam toda a história.
        </p>
      </div>
      <div role="tablist" aria-label="Feedback de" className="flex gap-1">
        {(["pessoas", "areas"] as const).map((v) => (
          <Button key={v} role="tab" aria-selected={visao === v} size="sm" variant={visao === v ? "default" : "outline"} onClick={() => setVisao(v)}>
            {v === "pessoas" ? `Pessoas (${f.pessoas.length})` : `Áreas (${f.areas.length})`}
          </Button>
        ))}
      </div>
      {lista.length === 0 ? (
        <Vazio titulo={visao === "pessoas" ? "Nenhuma tarefa com responsável" : "Nenhuma tarefa ligada a pilares"}>
          {visao === "pessoas" ? "Defina o responsável das tarefas para ver o feedback de cada pessoa." : "Ligue as tarefas aos pilares para ver o feedback de cada área."}
        </Vazio>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {lista.map((e) => <CartaoFeedback key={e.chave} projetoId={projetoId} entidade={e} tipo={visao === "pessoas" ? "pessoa" : "area"} />)}
        </div>
      )}
    </div>
  )
}
