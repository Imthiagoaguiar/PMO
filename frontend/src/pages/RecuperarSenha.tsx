import { KeyRound, MailCheck } from "lucide-react"
import { useEffect, useState, type FormEvent, type ReactNode } from "react"
import { Link, useLocation, useNavigate } from "react-router"
import { toast } from "sonner"

import { DICA_SENHA, SENHA_MIN } from "@/components/AlterarSenha"
import { Campo } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { ApiError, api } from "@/lib/api"
import { useAuth } from "@/lib/auth"

function Moldura({ icone, titulo, descricao, children }: {
  icone: ReactNode
  titulo: string
  descricao: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">{icone}</div>
          <CardTitle className="text-xl">{titulo}</CardTitle>
          <CardDescription>{descricao}</CardDescription>
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>
    </div>
  )
}

function Alerta({ texto }: { texto: string | null }) {
  if (!texto) return null
  return (
    <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {texto}
    </p>
  )
}

const VOLTAR = (
  <Link to="/login" className="text-center text-sm text-muted-foreground hover:underline">
    Voltar para o login
  </Link>
)

/** /esqueci-senha: pede o link. A resposta é a mesma exista ou não a conta. */
export function EsqueciSenha() {
  const location = useLocation()
  const [email, setEmail] = useState((location.state as { email?: string } | null)?.email ?? "")
  const [enviado, setEnviado] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)

  async function enviar(e: FormEvent) {
    e.preventDefault()
    setErro(null)
    setEnviando(true)
    try {
      const r = await api<{ mensagem: string }>("/auth/esqueci-senha", {
        method: "POST",
        json: { email: email.trim() },
        semRenovar: true,
      })
      setEnviado(r.mensagem)
    } catch (err) {
      setErro(err instanceof ApiError ? err.message : "Não foi possível enviar o pedido.")
    } finally {
      setEnviando(false)
    }
  }

  if (enviado) {
    return (
      <Moldura icone={<MailCheck className="size-5" />} titulo="Confira seu e-mail" descricao={enviado}>
        <div className="grid gap-4">
          <p className="text-sm text-muted-foreground">
            O link vale por 30 minutos e só pode ser usado uma vez. Se não chegar, confira o endereço digitado e
            peça de novo daqui a alguns minutos.
          </p>
          {VOLTAR}
        </div>
      </Moldura>
    )
  }

  return (
    <Moldura
      icone={<KeyRound className="size-5" />}
      titulo="Esqueci minha senha"
      descricao="Digite o e-mail da sua conta. Enviaremos um link para criar uma nova senha."
    >
      <form onSubmit={enviar} className="grid gap-4">
        <Campo label="E-mail" htmlFor="email">
          <Input id="email" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Campo>
        <Alerta texto={erro} />
        <Button carregando={enviando} type="submit" disabled={enviando || !email.trim()}>
          {enviando ? "Enviando..." : "Enviar link"}
        </Button>
        {VOLTAR}
      </form>
    </Moldura>
  )
}

/** Lê o token do fragmento (#token=...) e o tira da barra de endereço e do histórico. */
function useTokenDoLink(): string | null {
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("token"))
  useEffect(() => {
    if (window.location.hash) window.history.replaceState(window.history.state, "", window.location.pathname)
  }, [])
  return token
}

/** /redefinir-senha#token=...: aberto pelo link do e-mail. */
export function RedefinirSenha() {
  const token = useTokenDoLink()
  const { encerrarLocalmente } = useAuth()
  const navigate = useNavigate()
  const [nova, setNova] = useState("")
  const [confirmacao, setConfirmacao] = useState("")
  const [erro, setErro] = useState<string | null>(null)
  const [linkInvalido, setLinkInvalido] = useState(!token)
  const [enviando, setEnviando] = useState(false)

  const erroConfirmacao = confirmacao.length > 0 && confirmacao !== nova
  const valido = nova.length >= SENHA_MIN && nova === confirmacao

  async function enviar(e: FormEvent) {
    e.preventDefault()
    if (!valido || !token) return
    setErro(null)
    setEnviando(true)
    try {
      await api("/auth/redefinir-senha", { method: "POST", json: { token, nova_senha: nova }, semRenovar: true })
      // O back derrubou todas as sessões (inclusive uma aberta neste navegador).
      encerrarLocalmente()
      toast.success("Senha criada. Entre com a nova senha.")
      navigate("/login", { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) setLinkInvalido(true)
      else setErro(err instanceof ApiError ? err.message : "Não foi possível salvar a nova senha.")
    } finally {
      setEnviando(false)
    }
  }

  if (linkInvalido) {
    return (
      <Moldura
        icone={<KeyRound className="size-5" />}
        titulo="Link inválido ou expirado"
        descricao="O link vale por 30 minutos e uma única vez. Peça um novo para continuar."
      >
        <div className="grid gap-4">
          <Button asChild>
            <Link to="/esqueci-senha">Pedir um novo link</Link>
          </Button>
          {VOLTAR}
        </div>
      </Moldura>
    )
  }

  return (
    <Moldura icone={<KeyRound className="size-5" />} titulo="Criar uma nova senha" descricao="Depois disso, todas as sessões abertas da sua conta são encerradas.">
      <form onSubmit={enviar} className="grid gap-4">
        <Campo label="Nova senha" htmlFor="senha-nova" dica={DICA_SENHA}>
          <Input id="senha-nova" type="password" autoComplete="new-password" autoFocus value={nova} onChange={(e) => setNova(e.target.value)} />
        </Campo>
        <Campo label="Confirme a nova senha" htmlFor="senha-confirmacao">
          <Input
            id="senha-confirmacao"
            type="password"
            autoComplete="new-password"
            aria-invalid={erroConfirmacao}
            value={confirmacao}
            onChange={(e) => setConfirmacao(e.target.value)}
          />
          {erroConfirmacao && <p className="text-xs text-destructive">As senhas não conferem.</p>}
        </Campo>
        <Alerta texto={erro} />
        <Button carregando={enviando} type="submit" disabled={!valido || enviando}>
          {enviando ? "Salvando..." : "Salvar nova senha"}
        </Button>
      </form>
    </Moldura>
  )
}
