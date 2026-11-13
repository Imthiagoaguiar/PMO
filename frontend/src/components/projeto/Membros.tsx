import { useMutation, useQuery } from "@tanstack/react-query"
import { Crown, Search, UserMinus, UserPlus } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Carregando, ErroCarregar, NOMES_PAPEL_PROJETO } from "@/components/comum"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { useAtualizarProjeto, useMembros } from "@/lib/consultas"
import { notificarErro } from "@/lib/formato"
import { useAtrasado } from "@/lib/hooks"
import type { Membro, PapelProjeto, Projeto, UsuarioResumo } from "@/lib/tipos"

const DESCRICAO_PAPEL: Record<PapelProjeto, string> = {
  editor: "Altera dados e cronograma",
  visualizador: "Vê, exporta e comenta",
}

function SeletorPapel({ valor, aoMudar, desabilitado }: { valor: PapelProjeto; aoMudar: (p: PapelProjeto) => void; desabilitado?: boolean }) {
  return (
    <Select value={valor} onValueChange={(v) => aoMudar(v as PapelProjeto)} disabled={desabilitado}>
      <SelectTrigger className="w-40" size="sm">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.keys(NOMES_PAPEL_PROJETO) as PapelProjeto[]).map((p) => (
          <SelectItem key={p} value={p} title={DESCRICAO_PAPEL[p]}>
            {NOMES_PAPEL_PROJETO[p]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function AdicionarMembro({ projetoId, membros }: { projetoId: number; membros: Membro[] }) {
  const atualizar = useAtualizarProjeto(projetoId)
  const [busca, setBusca] = useState("")
  const [papel, setPapel] = useState<PapelProjeto>("visualizador")
  const termo = useAtrasado(busca.trim())
  const jaMembros = new Set(membros.map((m) => m.id))

  const resultados = useQuery({
    queryKey: ["diretorio", termo],
    queryFn: () => api<UsuarioResumo[]>(`/usuarios/diretorio?busca=${encodeURIComponent(termo)}`),
    enabled: termo.length >= 2,
  })

  const adicionar = useMutation({
    mutationFn: (u: UsuarioResumo) =>
      api<Membro[]>(`/projetos/${projetoId}/membros`, { method: "POST", json: { usuario_id: u.id, papel } }),
    onSuccess: (_, u) => {
      atualizar()
      toast.success(`${u.nome} agora é ${NOMES_PAPEL_PROJETO[papel].toLowerCase()} do projeto.`)
      setBusca("")
    },
    onError: notificarErro,
  })

  const candidatos = (resultados.data ?? []).filter((u) => !jaMembros.has(u.id))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Adicionar membro</CardTitle>
        <CardDescription>Busque pelo nome ou e-mail. Novos membros entram como visualizadores, a menos que você escolha outro papel.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap gap-2">
          <div className="relative min-w-60 flex-1">
            <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="pl-8" placeholder="Nome ou e-mail (mín. 2 letras)" value={busca} onChange={(e) => setBusca(e.target.value)} />
          </div>
          <SeletorPapel valor={papel} aoMudar={setPapel} />
        </div>
        {termo.length >= 2 && (
          <div className="rounded-md border">
            {resultados.isPending ? (
              <p className="px-3 py-2 text-sm text-muted-foreground">Buscando...</p>
            ) : candidatos.length === 0 ? (
              <p className="px-3 py-2 text-sm text-muted-foreground">Ninguém encontrado (ou já é membro).</p>
            ) : (
              <ul className="divide-y">
                {candidatos.map((u) => (
                  <li key={u.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <div className="truncate font-medium">{u.nome}</div>
                      <div className="truncate text-xs text-muted-foreground">{u.email}</div>
                    </div>
                    <Button size="sm" variant="outline" disabled={adicionar.isPending} onClick={() => adicionar.mutate(u)}>
                      <UserPlus /> Adicionar
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function Membros({ projeto }: { projeto: Projeto }) {
  const consulta = useMembros(projeto.id)
  const atualizar = useAtualizarProjeto(projeto.id)
  const gerenciar = !!projeto.permissoes?.gerenciar

  const alterarPapel = useMutation({
    mutationFn: ({ membro, papel }: { membro: Membro; papel: PapelProjeto }) =>
      api(`/projetos/${projeto.id}/membros/${membro.id}`, { method: "PATCH", json: { papel } }),
    onSuccess: (_, { membro, papel }) => {
      atualizar()
      toast.success(`${membro.nome} agora é ${NOMES_PAPEL_PROJETO[papel].toLowerCase()}.`)
    },
    onError: notificarErro,
  })

  const remover = useMutation({
    mutationFn: (membro: Membro) => api(`/projetos/${projeto.id}/membros/${membro.id}`, { method: "DELETE" }),
    onSuccess: (_, membro) => {
      atualizar()
      toast.success(`${membro.nome} foi removido. As tarefas dele ficaram sem responsável.`)
    },
    onError: notificarErro,
  })

  if (consulta.isPending) return <Carregando />
  if (consulta.isError) return <ErroCarregar erro={consulta.error} />

  return (
    <div className="grid gap-4">
      {gerenciar && <AdicionarMembro projetoId={projeto.id} membros={consulta.data} />}
      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-4">Membro</TableHead>
              <TableHead>Papel no projeto</TableHead>
              {gerenciar && <TableHead className="w-12 pr-4" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {consulta.data.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="pl-4">
                  <div className="flex items-center gap-2 font-medium">
                    {m.nome}
                    {m.dono && (
                      <Badge variant="secondary">
                        <Crown /> Dono
                      </Badge>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground">{m.email}</div>
                </TableCell>
                <TableCell>
                  {gerenciar && !m.dono ? (
                    <SeletorPapel
                      valor={m.papel}
                      desabilitado={alterarPapel.isPending}
                      aoMudar={(papel) => papel !== m.papel && alterarPapel.mutate({ membro: m, papel })}
                    />
                  ) : (
                    <span className="text-sm">{m.dono ? "Dono (edita e gerencia)" : NOMES_PAPEL_PROJETO[m.papel]}</span>
                  )}
                </TableCell>
                {gerenciar && (
                  <TableCell className="pr-4 text-right">
                    {!m.dono && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Remover ${m.nome}`}
                        disabled={remover.isPending}
                        onClick={() => window.confirm(`Remover ${m.nome} do projeto?`) && remover.mutate(m)}
                      >
                        <UserMinus />
                      </Button>
                    )}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        <strong className="font-medium">Editor:</strong> {DESCRICAO_PAPEL.editor.toLowerCase()}. <strong className="font-medium">Visualizador:</strong>{" "}
        {DESCRICAO_PAPEL.visualizador.toLowerCase()}. Só o dono e administradores gerenciam membros e excluem o projeto.
      </p>
    </div>
  )
}
