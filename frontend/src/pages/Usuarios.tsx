import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, LogOut, MoreHorizontal, Plus, Search, ShieldCheck, ShieldOff } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { Cabecalho, Campo, Carregando, ErroCarregar, NOMES_PAPEL_USUARIO, Paginacao, Vazio } from "@/components/comum"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { DICA_SENHA } from "@/components/AlterarSenha"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { useUsuario } from "@/lib/auth"
import { notificarErro } from "@/lib/formato"
import { useAtrasado } from "@/lib/hooks"
import type { Pagina, PapelUsuario, Usuario } from "@/lib/tipos"

const LIMITE = 20
const PAPEIS = Object.keys(NOMES_PAPEL_USUARIO) as PapelUsuario[]
const DESCRICAO_PAPEL: Record<PapelUsuario, string> = {
  admin: "Gerencia usuários e vê todos os projetos",
  gerente: "Cria projetos e gerencia os seus",
  membro: "Participa dos projetos para os quais for adicionado",
}

function NovoUsuario({ aoFechar }: { aoFechar: () => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const [nome, setNome] = useState("")
  const [papel, setPapel] = useState<PapelUsuario>("membro")
  const [provedor, setProvedor] = useState<"local" | "clerk">("local")
  const [senha, setSenha] = useState("")

  const criar = useMutation({
    mutationFn: () =>
      api<Usuario>("/usuarios", {
        method: "POST",
        json: { email: email.trim(), nome: nome.trim(), papel, provedor_auth: provedor, ...(provedor === "local" && { senha }) },
      }),
    onSuccess: (u) => {
      queryClient.invalidateQueries({ queryKey: ["usuarios"] })
      toast.success(
        provedor === "local" ? `${u.nome} criado. Envie a senha temporária por um canal seguro.` : `${u.nome} poderá entrar pelo Clerk com ${u.email}.`,
      )
      aoFechar()
    },
    onError: notificarErro,
  })

  function enviar(e: FormEvent) {
    e.preventDefault()
    criar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Novo usuário</DialogTitle>
            <DialogDescription>O papel define o que a pessoa pode fazer no sistema todo.</DialogDescription>
          </DialogHeader>
          <Campo label="Nome" htmlFor="u-nome">
            <Input id="u-nome" required minLength={2} maxLength={150} value={nome} onChange={(e) => setNome(e.target.value)} />
          </Campo>
          <Campo label="E-mail" htmlFor="u-email">
            <Input id="u-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </Campo>
          <Campo label="Papel" dica={DESCRICAO_PAPEL[papel]}>
            <Select value={papel} onValueChange={(v) => setPapel(v as PapelUsuario)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAPEIS.map((p) => (
                  <SelectItem key={p} value={p}>
                    {NOMES_PAPEL_USUARIO[p]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Campo>
          <Campo
            label="Forma de login"
            dica={provedor === "clerk" ? "Sem senha aqui: a conta é vinculada no primeiro login pelo Clerk com este e-mail." : undefined}
          >
            <Select value={provedor} onValueChange={(v) => setProvedor(v as "local" | "clerk")}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local">E-mail e senha</SelectItem>
                <SelectItem value="clerk">Clerk</SelectItem>
              </SelectContent>
            </Select>
          </Campo>
          {provedor === "local" && (
            <Campo label="Senha temporária" htmlFor="u-senha" dica={`A pessoa será obrigada a trocá-la no primeiro acesso. ${DICA_SENHA}`}>
              <Input id="u-senha" type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={senha} onChange={(e) => setSenha(e.target.value)} />
            </Campo>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>
              Cancelar
            </Button>
            <Button type="submit" disabled={criar.isPending}>
              {criar.isPending ? "Criando..." : "Criar usuário"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function RedefinirSenha({ usuario, aoFechar }: { usuario: Usuario; aoFechar: () => void }) {
  const queryClient = useQueryClient()
  const [senha, setSenha] = useState("")

  const redefinir = useMutation({
    mutationFn: () => api(`/usuarios/${usuario.id}/redefinir-senha`, { method: "POST", json: { nova_senha: senha } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["usuarios"] })
      toast.success(`Senha de ${usuario.nome} redefinida. Envie a senha temporária por um canal seguro.`)
      aoFechar()
    },
    onError: notificarErro,
  })

  function enviar(e: FormEvent) {
    e.preventDefault()
    redefinir.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Redefinir senha de {usuario.nome}</DialogTitle>
            <DialogDescription>
              As sessões abertas caem na hora, o bloqueio por tentativas é desfeito e a pessoa precisa trocar a senha no próximo login.
            </DialogDescription>
          </DialogHeader>
          <Campo label="Senha temporária" htmlFor="r-senha" dica={DICA_SENHA}>
            <Input id="r-senha" type="password" autoComplete="new-password" required minLength={12} maxLength={128} autoFocus value={senha} onChange={(e) => setSenha(e.target.value)} />
          </Campo>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>
              Cancelar
            </Button>
            <Button type="submit" disabled={redefinir.isPending || senha.length < 12}>
              {redefinir.isPending ? "Redefinindo..." : "Redefinir senha"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function Usuarios() {
  const eu = useUsuario()
  const queryClient = useQueryClient()
  const [busca, setBusca] = useState("")
  const [papel, setPapel] = useState("todos")
  const [offset, setOffset] = useState(0)
  const [criando, setCriando] = useState(false)
  const [redefinindo, setRedefinindo] = useState<Usuario | null>(null)
  const termo = useAtrasado(busca.trim())

  const parametros = new URLSearchParams({ limite: String(LIMITE), offset: String(offset) })
  if (termo) parametros.set("busca", termo)
  if (papel !== "todos") parametros.set("papel", papel)

  const consulta = useQuery({
    queryKey: ["usuarios", parametros.toString()],
    queryFn: () => api<Pagina<Usuario>>(`/usuarios?${parametros}`),
    placeholderData: keepPreviousData,
  })

  const atualizar = useMutation({
    mutationFn: ({ usuario, dados }: { usuario: Usuario; dados: Partial<Pick<Usuario, "papel" | "ativo">> }) =>
      api<Usuario>(`/usuarios/${usuario.id}`, { method: "PATCH", json: dados }),
    onSuccess: (u, { dados }) => {
      queryClient.invalidateQueries({ queryKey: ["usuarios"] })
      if (dados.ativo === false) toast.success(`${u.nome} foi desativado e perdeu o acesso imediatamente.`)
      else if (dados.ativo === true) toast.success(`${u.nome} foi reativado.`)
      else toast.success(`${u.nome} agora é ${NOMES_PAPEL_USUARIO[u.papel].toLowerCase()}.`)
    },
    onError: notificarErro,
  })

  const redefinir2fa = useMutation({
    mutationFn: (u: Usuario) => api(`/usuarios/${u.id}/redefinir-2fa`, { method: "POST" }),
    onSuccess: (_, u) => {
      queryClient.invalidateQueries({ queryKey: ["usuarios"] })
      toast.success(`2FA de ${u.nome} desligado. ${u.papel === "admin" ? "Terá de ativar de novo ao entrar." : ""}`)
    },
    onError: notificarErro,
  })

  const encerrarSessoes = useMutation({
    mutationFn: (u: Usuario) => api(`/usuarios/${u.id}/encerrar-sessoes`, { method: "POST" }),
    onSuccess: (_, u) => toast.success(`${u.nome} foi desconectado de todos os dispositivos.`),
    onError: notificarErro,
  })

  return (
    <>
      <Cabecalho titulo="Usuários" descricao="Quem acessa o PMO e com qual papel.">
        <Button onClick={() => setCriando(true)}>
          <Plus /> Novo usuário
        </Button>
      </Cabecalho>

      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-8"
            placeholder="Nome ou e-mail..."
            value={busca}
            onChange={(e) => {
              setBusca(e.target.value)
              setOffset(0)
            }}
          />
        </div>
        <Select
          value={papel}
          onValueChange={(v) => {
            setPapel(v)
            setOffset(0)
          }}
        >
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="todos">Todos os papéis</SelectItem>
            {PAPEIS.map((p) => (
              <SelectItem key={p} value={p}>
                {NOMES_PAPEL_USUARIO[p]}
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
        <Vazio titulo="Nenhum usuário encontrado" />
      ) : (
        <>
          <div className="rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Usuário</TableHead>
                  <TableHead>Login</TableHead>
                  <TableHead>Papel</TableHead>
                  <TableHead className="pr-4 text-right">Situação</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {consulta.data.itens.map((u) => {
                  const souEu = u.id === eu.id
                  return (
                    <TableRow key={u.id} className={u.ativo ? undefined : "opacity-60"}>
                      <TableCell className="pl-4">
                        <div className="font-medium">
                          {u.nome} {souEu && <span className="text-xs font-normal text-muted-foreground">(você)</span>}
                        </div>
                        <div className="text-xs text-muted-foreground">{u.email}</div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          <Badge variant="outline">{u.provedor_auth === "local" ? "E-mail e senha" : u.provedor_auth === "clerk" ? "Clerk" : u.provedor_auth}</Badge>
                          {u.aguardando_primeiro_login && <Badge variant="secondary">Aguardando 1º acesso</Badge>}
                          {u.trocar_senha && <Badge variant="secondary">Troca de senha pendente</Badge>}
                          {u.dois_fatores_ativo && (
                            <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">
                              <ShieldCheck /> 2FA
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Select
                          value={u.papel}
                          disabled={souEu || atualizar.isPending}
                          onValueChange={(v) => v !== u.papel && atualizar.mutate({ usuario: u, dados: { papel: v as PapelUsuario } })}
                        >
                          <SelectTrigger className="w-40" size="sm" aria-label={`Papel de ${u.nome}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {PAPEIS.map((p) => (
                              <SelectItem key={p} value={p}>
                                {NOMES_PAPEL_USUARIO[p]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell className="pr-4">
                        <div className="flex justify-end gap-1">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={souEu || atualizar.isPending}
                            onClick={() => {
                              if (u.ativo && !window.confirm(`Desativar ${u.nome}? O acesso é cortado na hora.`)) return
                              atualizar.mutate({ usuario: u, dados: { ativo: !u.ativo } })
                            }}
                          >
                            {u.ativo ? "Desativar" : "Reativar"}
                          </Button>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button size="icon-sm" variant="ghost" aria-label={`Mais ações para ${u.nome}`}>
                                <MoreHorizontal />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem disabled={souEu || u.provedor_auth !== "local"} onSelect={() => setRedefinindo(u)}>
                                <KeyRound /> Redefinir senha
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                disabled={souEu || !u.dois_fatores_ativo}
                                onSelect={() => {
                                  const aviso =
                                    `Desligar a verificação em duas etapas de ${u.nome}? Use só se a pessoa perdeu o celular E os códigos ` +
                                    "de recuperação, depois de confirmar a identidade dela por outro canal."
                                  if (window.confirm(aviso)) redefinir2fa.mutate(u)
                                }}
                              >
                                <ShieldOff /> Redefinir 2FA
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onSelect={() => {
                                  const aviso = souEu
                                    ? "Encerrar todas as SUAS sessões? Você também será desconectado aqui."
                                    : `Desconectar ${u.nome} de todos os dispositivos?`
                                  if (window.confirm(aviso)) encerrarSessoes.mutate(u)
                                }}
                              >
                                <LogOut /> Encerrar sessões
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
          <Paginacao total={consulta.data.total} limite={LIMITE} offset={offset} aoMudar={setOffset} />
        </>
      )}

      {criando && <NovoUsuario aoFechar={() => setCriando(false)} />}
      {redefinindo && <RedefinirSenha usuario={redefinindo} aoFechar={() => setRedefinindo(null)} />}
    </>
  )
}
