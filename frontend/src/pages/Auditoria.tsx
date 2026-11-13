import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router"

import { Cabecalho, Carregando, ErroCarregar, Paginacao, Vazio } from "@/components/comum"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { ACOES, descreverAcao, descreverDetalhes } from "@/lib/auditoria"
import * as fmt from "@/lib/formato"
import type { EventoAuditoria, Pagina } from "@/lib/tipos"

const LIMITE = 50

export function Auditoria() {
  const [acao, setAcao] = useState("todas")
  const [offset, setOffset] = useState(0)

  const parametros = new URLSearchParams({ limite: String(LIMITE), offset: String(offset) })
  if (acao !== "todas") parametros.set("acao", acao)

  const consulta = useQuery({
    queryKey: ["auditoria", parametros.toString()],
    queryFn: () => api<Pagina<EventoAuditoria>>(`/auditoria?${parametros}`),
    placeholderData: keepPreviousData,
  })

  return (
    <>
      <Cabecalho titulo="Auditoria" descricao="Tudo o que acontece no sistema: quem, o quê, quando e de onde." />

      <div className="mb-4">
        <Select
          value={acao}
          onValueChange={(v) => {
            setAcao(v)
            setOffset(0)
          }}
        >
          <SelectTrigger className="w-80">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="todas">Todas as ações</SelectItem>
            {Object.entries(ACOES).map(([chave, texto]) => (
              <SelectItem key={chave} value={chave}>
                {texto} <span className="text-xs text-muted-foreground">({chave})</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {consulta.isPending ? (
        <Carregando />
      ) : consulta.isError ? (
        <ErroCarregar erro={consulta.error} />
      ) : consulta.data.itens.length === 0 ? (
        <Vazio titulo="Nenhum evento encontrado" />
      ) : (
        <>
          <div className="rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Quando</TableHead>
                  <TableHead>Quem</TableHead>
                  <TableHead>O quê</TableHead>
                  <TableHead>Detalhes</TableHead>
                  <TableHead className="pr-4">IP</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {consulta.data.itens.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell className="pl-4 whitespace-nowrap tabular-nums">{fmt.dataHora(e.criado_em)}</TableCell>
                    <TableCell>{e.usuario?.nome ?? <span className="text-muted-foreground">—</span>}</TableCell>
                    <TableCell>
                      {descreverAcao(e.acao)}
                      {e.projeto_id && (
                        <Link to={`/projetos/${e.projeto_id}`} className="ml-1 text-xs text-primary hover:underline">
                          projeto #{e.projeto_id}
                        </Link>
                      )}
                    </TableCell>
                    <TableCell className="max-w-md text-xs whitespace-normal text-muted-foreground">
                      {descreverDetalhes(e).join(" · ")}
                    </TableCell>
                    <TableCell className="pr-4 text-xs text-muted-foreground">{e.ip ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Paginacao total={consulta.data.total} limite={LIMITE} offset={offset} aoMudar={setOffset} />
        </>
      )}
    </>
  )
}
