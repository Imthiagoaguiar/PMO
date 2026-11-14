import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronRight, Clock, ListChecks } from "lucide-react"
import { useState } from "react"

import { Cabecalho, Carregando, ErroCarregar, Paginacao, Vazio } from "@/components/comum"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { api } from "@/lib/api"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { AchadoVigia, GravidadeAchado, Pagina, VigiaRodada, VigiaRodadaResumo, VigiaSituacao } from "@/lib/tipos"

const LIMITE = 24

const GRAVIDADE: Record<GravidadeAchado, { rotulo: string; classe: string }> = {
  critica: { rotulo: "Crítica", classe: "bg-red-600 text-white" },
  alta: { rotulo: "Alta", classe: "bg-orange-500 text-white" },
  media: { rotulo: "Média", classe: "bg-amber-400 text-amber-950" },
  baixa: { rotulo: "Baixa", classe: "bg-slate-200 text-slate-800" },
}

function SeloGravidade({ gravidade }: { gravidade: GravidadeAchado | null }) {
  if (!gravidade) {
    return (
      <Badge variant="outline" className="gap-1 border-emerald-500/40 text-emerald-700">
        <CheckCircle2 className="size-3" /> Sem achados
      </Badge>
    )
  }
  return <Badge className={cn("border-0", GRAVIDADE[gravidade].classe)}>{GRAVIDADE[gravidade].rotulo}</Badge>
}

/** Relatórios do vigia (python -m app.vigia): só aparece para os admins listados em VIGIA_EMAILS. */
export function Vigia() {
  const [offset, setOffset] = useState(0)
  const [aberta, setAberta] = useState<number | null>(null)

  const situacao = useQuery({ queryKey: ["vigia", "situacao"], queryFn: () => api<VigiaSituacao>("/vigia/situacao"), refetchInterval: 60_000 })
  const rodadas = useQuery({
    queryKey: ["vigia", "rodadas", offset],
    queryFn: () => api<Pagina<VigiaRodadaResumo>>(`/vigia/rodadas?limite=${LIMITE}&offset=${offset}`),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  const selecionada = aberta ?? rodadas.data?.itens[0]?.id ?? null

  return (
    <>
      <Cabecalho
        titulo="Vigia"
        descricao="Agente que procura erros e possíveis falhas no sistema rodando, de hora em hora. Ele só observa: nunca altera código nem dados."
      />
      {situacao.data && (
        <div
          className={cn(
            "mb-4 flex items-center gap-2 rounded-md border px-4 py-2.5 text-sm",
            situacao.data.atrasado ? "border-amber-300 bg-amber-50 text-amber-900" : "bg-card text-muted-foreground",
          )}
        >
          {situacao.data.atrasado ? <AlertTriangle className="size-4" /> : <Clock className="size-4" />}
          {situacao.data.ultima_rodada_em
            ? `Última rodada em ${fmt.dataHora(situacao.data.ultima_rodada_em)} (a cada ${situacao.data.intervalo_minutos} min).`
            : "O vigia ainda não rodou."}
          {situacao.data.atrasado && (
            <span className="font-medium">
              Ele parece parado: rode <code className="rounded bg-amber-100 px-1">python -m app.vigia</code> ao lado da API.
            </span>
          )}
        </div>
      )}

      {rodadas.isPending ? (
        <Carregando />
      ) : rodadas.isError ? (
        <ErroCarregar erro={rodadas.error} />
      ) : rodadas.data.itens.length === 0 ? (
        <Vazio titulo="Nenhuma rodada ainda">Os relatórios aparecem aqui assim que o vigia fizer a primeira rodada.</Vazio>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
          <div>
            <ol className="grid gap-1.5">
              {rodadas.data.itens.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => setAberta(r.id)}
                    className={cn(
                      "w-full rounded-md border bg-card px-3 py-2.5 text-left text-sm transition-colors hover:bg-muted/60",
                      selecionada === r.id && "border-primary ring-1 ring-primary",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium tabular-nums">{fmt.dataHora(r.iniciada_em)}</span>
                      <SeloGravidade gravidade={r.pior_gravidade} />
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{r.resumo}</p>
                  </button>
                </li>
              ))}
            </ol>
            <Paginacao total={rodadas.data.total} limite={LIMITE} offset={offset} aoMudar={setOffset} />
          </div>
          {selecionada !== null && <DetalheRodada id={selecionada} />}
        </div>
      )}
    </>
  )
}

function DetalheRodada({ id }: { id: number }) {
  const consulta = useQuery({ queryKey: ["vigia", "rodada", id], queryFn: () => api<VigiaRodada>(`/vigia/rodadas/${id}`) })
  const [verSinais, setVerSinais] = useState(false)
  if (consulta.isPending) return <Carregando />
  if (consulta.isError) return <ErroCarregar erro={consulta.error} />
  const r = consulta.data
  const ordem: GravidadeAchado[] = ["critica", "alta", "media", "baixa"]
  const achados = [...r.achados].sort((a, b) => ordem.indexOf(a.gravidade) - ordem.indexOf(b.gravidade))

  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-3">
          Rodada de {fmt.dataHora(r.iniciada_em)} <SeloGravidade gravidade={r.pior_gravidade} />
        </CardTitle>
        <CardDescription>
          Janela desde {fmt.dataHora(r.janela_inicio)} · <Bot className="inline size-3.5" /> {r.ia_situacao}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-sm">{r.resumo}</p>
        {achados.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-emerald-700">
            <CheckCircle2 className="size-4" /> Nenhum erro ou falha encontrado nesta janela.
          </p>
        ) : (
          <ul className="grid gap-3">
            {achados.map((a, i) => (
              <ItemAchado key={i} achado={a} />
            ))}
          </ul>
        )}
        <div>
          <button type="button" onClick={() => setVerSinais(!verSinais)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            {verSinais ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />} Sinais coletados (dados brutos, já sem e-mails e IPs)
          </button>
          {verSinais && (
            <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(r.sinais, null, 2)}</pre>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function ItemAchado({ achado: a }: { achado: AchadoVigia }) {
  return (
    <li className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <SeloGravidade gravidade={a.gravidade} />
        <span className="font-medium">{a.titulo}</span>
        <Badge variant="outline" className="ml-auto font-normal text-muted-foreground">
          {a.origem === "ia" ? (
            <>
              <Bot className="size-3" /> análise da IA
            </>
          ) : (
            <>
              <ListChecks className="size-3" /> regra
            </>
          )}
        </Badge>
      </div>
      {a.evidencias.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-muted-foreground">
          {a.evidencias.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
      {a.causa_provavel && (
        <p className="mt-2 text-xs">
          <span className="font-medium">Causa provável:</span> {a.causa_provavel}
        </p>
      )}
      {a.impacto && (
        <p className="mt-1 text-xs">
          <span className="font-medium">Impacto:</span> {a.impacto}
        </p>
      )}
      {a.como_investigar.length > 0 && (
        <div className="mt-2 text-xs">
          <span className="font-medium">Como investigar:</span>
          <ul className="list-disc pl-5">
            {a.como_investigar.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </li>
  )
}
