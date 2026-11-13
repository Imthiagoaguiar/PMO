import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Plus, Search } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"

import { Cabecalho, Carregando, ErroCarregar, Paginacao, StatusBadge, Vazio } from "@/components/comum"
import { NovoProjeto } from "@/components/NovoProjeto"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { podeCriarProjeto, useUsuario } from "@/lib/auth"
import * as fmt from "@/lib/formato"
import { useAtrasado } from "@/lib/hooks"
import { STATUS_PROJETO, type Pagina, type ProjetoResumo } from "@/lib/tipos"

const LIMITE = 20

export function Projetos() {
  const usuario = useUsuario()
  const [busca, setBusca] = useState("")
  const [status, setStatus] = useState("todos")
  const [offset, setOffset] = useState(0)
  const [criando, setCriando] = useState(false)
  const buscaAtrasada = useAtrasado(busca.trim())

  const parametros = new URLSearchParams({ limite: String(LIMITE), offset: String(offset) })
  if (buscaAtrasada) parametros.set("busca", buscaAtrasada)
  if (status !== "todos") parametros.set("status", status)

  const consulta = useQuery({
    queryKey: ["projetos", parametros.toString()],
    queryFn: () => api<Pagina<ProjetoResumo>>(`/projetos?${parametros}`),
    placeholderData: keepPreviousData,
  })

  const filtrando = !!buscaAtrasada || status !== "todos"

  return (
    <>
      <Cabecalho titulo="Projetos" descricao="Projetos dos quais você participa.">
        {podeCriarProjeto(usuario) && (
          <Button onClick={() => setCriando(true)}>
            <Plus /> Novo projeto
          </Button>
        )}
      </Cabecalho>

      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Buscar por nome..."
            className="pl-8"
            value={busca}
            onChange={(e) => {
              setBusca(e.target.value)
              setOffset(0)
            }}
          />
        </div>
        <Select
          value={status}
          onValueChange={(v) => {
            setStatus(v)
            setOffset(0)
          }}
        >
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="todos">Todos os status</SelectItem>
            {STATUS_PROJETO.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
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
        <Vazio titulo={filtrando ? "Nenhum projeto encontrado" : "Você ainda não participa de nenhum projeto"}>
          {filtrando
            ? "Tente outros filtros."
            : podeCriarProjeto(usuario)
              ? "Crie o primeiro com o botão “Novo projeto”."
              : "Peça ao gerente do projeto para adicionar você."}
        </Vazio>
      ) : (
        <>
          <div className="rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Projeto</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Início</TableHead>
                  <TableHead className="text-right">Orçamento aprovado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {consulta.data.itens.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="font-medium">
                      <Link to={`/projetos/${p.id}`} className="hover:underline">
                        {p.nome_projeto}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={p.status} />
                    </TableCell>
                    <TableCell>{fmt.data(p.data_inicio)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmt.moeda(p.orcamento_aprovado)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Paginacao total={consulta.data.total} limite={LIMITE} offset={offset} aoMudar={setOffset} />
        </>
      )}

      <NovoProjeto aberto={criando} aoFechar={() => setCriando(false)} />
    </>
  )
}
