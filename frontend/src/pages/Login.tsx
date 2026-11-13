import { useState, type FormEvent } from "react"
import { Navigate, useLocation, useNavigate } from "react-router"

import { Campo } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { ApiError } from "@/lib/api"
import { useAuth } from "@/lib/auth"

export function Login() {
  const { usuario, entrar } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const destino = (location.state as { de?: string } | null)?.de ?? "/projetos"

  const [email, setEmail] = useState("")
  const [senha, setSenha] = useState("")
  const [erro, setErro] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)

  if (usuario) return <Navigate to={destino} replace />

  async function enviar(e: FormEvent) {
    e.preventDefault()
    setErro(null)
    setEnviando(true)
    try {
      await entrar(email.trim(), senha)
      navigate(destino, { replace: true })
    } catch (err) {
      setErro(err instanceof ApiError ? err.message : "Não foi possível entrar.")
      setSenha("")
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
            PMO
          </div>
          <CardTitle className="text-xl">Entrar</CardTitle>
          <CardDescription>Gestão de projetos Waterfall</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={enviar} className="grid gap-4">
            <Campo label="E-mail" htmlFor="email">
              <Input id="email" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
            </Campo>
            <Campo label="Senha" htmlFor="senha">
              <Input id="senha" type="password" autoComplete="current-password" required value={senha} onChange={(e) => setSenha(e.target.value)} />
            </Campo>
            {erro && (
              <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {erro}
              </p>
            )}
            <Button type="submit" disabled={enviando}>
              {enviando ? "Entrando..." : "Entrar"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
