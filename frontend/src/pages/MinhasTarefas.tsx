import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router"

import { BarraProgresso, Cabecalho, Carregando, ErroCarregar, Paginacao, Vazio } from "@/components/comum"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { MinhaTarefa, Pagina } from "@/lib/tipos"

const LIMITE = 20

export function MinhasTarefas() {
  const [pendentes, setPendentes] = useState(true)
  const [offset, setOffset] = useState(0)
  const consulta = useQuery({
    queryKey: ["minhas-tarefas", pendentes, offset],
    queryFn: () => api<Pagina<MinhaTarefa>>(`/tarefas/minhas?pendentes=${pendentes}&limite=${LIMITE}&offset=${offset}`),
    placeholderData: keepPreviousData,
  })

  return (
    <>
      <Cabecalho titulo="Minhas tarefas" descricao="Tarefas em que você é o responsável, por prazo.">
        <div className="flex rounded-md border p-0.5">
          {[
            [true, "Pendentes"],
            [false, "Todas"],
          ].map(([valor, rotulo]) => (
            <Button
              key={String(valor)}
              size="sm"
              variant={pendentes === valor ? "secondary" : "ghost"}
              onClick={() => {
                setPendentes(valor as boolean)
                setOffset(0)
              }}
            >
              {rotulo}
            </Button>
          ))}
        </div>
      </Cabecalho>

      {consulta.isPending ? (
        <Carregando />
      ) : consulta.isError ? (
        <ErroCarregar erro={consulta.error} />
      ) : consulta.data.itens.length === 0 ? (
        <Vazio titulo={pendentes ? "Nenhuma tarefa pendente com você" : "Nenhuma tarefa atribuída a você"} />
      ) : (
        <>
          <div className="rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Tarefa</TableHead>
                  <TableHead>Projeto</TableHead>
                  <TableHead>Fase</TableHead>
                  <TableHead>Término</TableHead>
                  <TableHead className="w-44 pr-4">Andamento</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {consulta.data.itens.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="max-w-sm pl-4 whitespace-normal">{t.descricao}</TableCell>
                    <TableCell>
                      <Link to={`/projetos/${t.projeto_id}`} className="font-medium hover:underline">
                        {t.nome_projeto}
                      </Link>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{t.fase_titulo}</TableCell>
                    <TableCell className={cn("tabular-nums", t.atrasada && "font-medium text-destructive")}>
                      {fmt.data(t.data_fim)}
                      {t.atrasada && (
                        <Badge variant="destructive" className="ml-2">
                          Atrasada
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="pr-4">
                      <BarraProgresso valor={t.percentual_conclusao} />
                    </TableCell>
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
