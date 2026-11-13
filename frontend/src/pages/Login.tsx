import { ShieldCheck } from "lucide-react"
import { useState, type FormEvent } from "react"
import { Navigate, useLocation, useNavigate } from "react-router"

import { Campo } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { ApiError } from "@/lib/api"
import { useAuth } from "@/lib/auth"

function mensagem(err: unknown) {
  return err instanceof ApiError ? err.message : "Não foi possível entrar."
}

export function Login() {
  const { usuario, entrar, entrarComCodigo } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const destino = (location.state as { de?: string } | null)?.de ?? "/projetos"

  const [email, setEmail] = useState("")
  const [senha, setSenha] = useState("")
  const [desafio, setDesafio] = useState<string | null>(null)
  const [codigo, setCodigo] = useState("")
  const [usandoRecuperacao, setUsandoRecuperacao] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)

  if (usuario) return <Navigate to={destino} replace />

  async function enviarSenha(e: FormEvent) {
    e.preventDefault()
    setErro(null)
    setEnviando(true)
    try {
      const resultado = await entrar(email.trim(), senha)
      if (resultado.requer2fa) setDesafio(resultado.desafio)
      else navigate(destino, { replace: true })
    } catch (err) {
      setErro(mensagem(err))
    } finally {
      setSenha("") // não fica na memória da tela
      setEnviando(false)
    }
  }

  async function enviarCodigo(e: FormEvent) {
    e.preventDefault()
    if (!desafio) return
    setErro(null)
    setEnviando(true)
    try {
      await entrarComCodigo(desafio, codigo.trim())
      navigate(destino, { replace: true })
    } catch (err) {
      setCodigo("")
      if (err instanceof ApiError && err.message.includes("Entre novamente")) recomecar(err.message)
      else setErro(mensagem(err))
    } finally {
      setEnviando(false)
    }
  }

  function recomecar(aviso: string | null = null) {
    setDesafio(null)
    setCodigo("")
    setUsandoRecuperacao(false)
    setErro(aviso)
  }

  const alerta = erro && (
    <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {erro}
    </p>
  )

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        {!desafio ? (
          <>
            <CardHeader>
              <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
                PMO
              </div>
              <CardTitle className="text-xl">Entrar</CardTitle>
              <CardDescription>Gestão de projetos Waterfall</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={enviarSenha} className="grid gap-4">
                <Campo label="E-mail" htmlFor="email">
                  <Input id="email" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
                </Campo>
                <Campo label="Senha" htmlFor="senha">
                  <Input id="senha" type="password" autoComplete="current-password" required value={senha} onChange={(e) => setSenha(e.target.value)} />
                </Campo>
                {alerta}
                <Button type="submit" disabled={enviando}>
                  {enviando ? "Entrando..." : "Entrar"}
                </Button>
              </form>
            </CardContent>
          </>
        ) : (
          <>
            <CardHeader>
              <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <ShieldCheck className="size-5" />
              </div>
              <CardTitle className="text-xl">Verificação em duas etapas</CardTitle>
              <CardDescription>
                {usandoRecuperacao
                  ? "Digite um dos seus códigos de recuperação. Cada um vale uma única vez."
                  : "Digite o código de 6 dígitos do seu app autenticador."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={enviarCodigo} className="grid gap-4">
                {usandoRecuperacao ? (
                  <Campo label="Código de recuperação" htmlFor="codigo">
                    <Input
                      id="codigo"
                      autoComplete="off"
                      autoFocus
                      placeholder="XXXXX-XXXXX"
                      maxLength={20}
                      className="font-mono tracking-wider uppercase"
                      value={codigo}
                      onChange={(e) => setCodigo(e.target.value)}
                    />
                  </Campo>
                ) : (
                  <Campo label="Código" htmlFor="codigo">
                    <Input
                      id="codigo"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      autoFocus
                      placeholder="000000"
                      maxLength={6}
                      className="text-center font-mono text-lg tracking-[0.5em]"
                      value={codigo}
                      onChange={(e) => setCodigo(e.target.value.replace(/\D/g, ""))}
                    />
                  </Campo>
                )}
                {alerta}
                <Button type="submit" disabled={enviando || (usandoRecuperacao ? codigo.trim().length < 10 : codigo.length !== 6)}>
                  {enviando ? "Verificando..." : "Verificar"}
                </Button>
                <div className="flex justify-between text-sm">
                  <button
                    type="button"
                    className="text-primary hover:underline"
                    onClick={() => {
                      setUsandoRecuperacao(!usandoRecuperacao)
                      setCodigo("")
                      setErro(null)
                    }}
                  >
                    {usandoRecuperacao ? "Usar o app autenticador" : "Perdi o celular"}
                  </button>
                  <button type="button" className="text-muted-foreground hover:underline" onClick={() => recomecar()}>
                    Voltar
                  </button>
                </div>
              </form>
            </CardContent>
          </>
        )}
      </Card>
    </div>
  )
}
