import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { Carregando, ErroCarregar, Paginacao, Vazio } from "@/components/comum"
import { api } from "@/lib/api"
import { descreverAcao, descreverDetalhes } from "@/lib/auditoria"
import { chaves } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import type { EventoAuditoria, Pagina } from "@/lib/tipos"

const LIMITE = 30

export function Historico({ projetoId }: { projetoId: number }) {
  const [offset, setOffset] = useState(0)
  const consulta = useQuery({
    queryKey: chaves.historico(projetoId, offset),
    queryFn: () => api<Pagina<EventoAuditoria>>(`/projetos/${projetoId}/historico?limite=${LIMITE}&offset=${offset}`),
    placeholderData: keepPreviousData,
  })

  if (consulta.isPending) return <Carregando />
  if (consulta.isError) return <ErroCarregar erro={consulta.error} />
  if (consulta.data.itens.length === 0) return <Vazio titulo="Nenhuma alteração registrada" />

  return (
    <>
      <ol className="relative grid gap-4 border-l pl-6">
        {consulta.data.itens.map((e) => {
          const detalhes = descreverDetalhes(e)
          return (
            <li key={e.id} className="relative">
              <span className="absolute top-1.5 -left-[1.82rem] size-2.5 rounded-full border-2 border-background bg-primary" />
              <p className="text-sm">
                <span className="font-medium">{e.usuario?.nome ?? "Sistema"}</span> {descreverAcao(e.acao)}
              </p>
              {detalhes.length > 0 && (
                <p className="mt-0.5 text-xs text-muted-foreground">{detalhes.join(" · ")}</p>
              )}
              <p className="mt-0.5 text-xs text-muted-foreground">{fmt.dataHora(e.criado_em)}</p>
            </li>
          )
        })}
      </ol>
      <Paginacao total={consulta.data.total} limite={LIMITE} offset={offset} aoMudar={setOffset} />
    </>
  )
}
