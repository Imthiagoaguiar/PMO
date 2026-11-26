import { useMutation } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { Campo } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { notificarErro } from "@/lib/formato"
import { TelaDaMarca } from "@/components/Logo"

export const SENHA_MIN = 12
export const DICA_SENHA =
  `Mínimo de ${SENHA_MIN} caracteres. Prefira uma frase (ex.: três ou quatro palavras sem relação). ` +
  "Senhas comuns, com seu nome ou já vazadas na internet são recusadas."

function FormularioSenha({ aoCancelar }: { aoCancelar?: () => void }) {
  const { encerrarLocalmente } = useAuth()
  const navigate = useNavigate()
  const [atual, setAtual] = useState("")
  const [nova, setNova] = useState("")
  const [confirmacao, setConfirmacao] = useState("")

  const alterar = useMutation({
    mutationFn: () => api("/auth/alterar-senha", { method: "POST", json: { senha_atual: atual, nova_senha: nova } }),
    onSuccess: () => {
      // O back derruba todas as sessões ao trocar a senha: entra de novo com a nova.
      encerrarLocalmente()
      toast.success("Senha alterada. Entre novamente com a nova senha.")
      navigate("/login")
    },
    onError: notificarErro,
  })

  const erroConfirmacao = confirmacao.length > 0 && confirmacao !== nova
  const valido = atual.length > 0 && nova.length >= SENHA_MIN && nova === confirmacao && nova !== atual

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (valido) alterar.mutate()
  }

  return (
    <form onSubmit={enviar} className="grid gap-4">
      <Campo label="Senha atual" htmlFor="senha-atual">
        <Input id="senha-atual" type="password" autoComplete="current-password" autoFocus value={atual} onChange={(e) => setAtual(e.target.value)} />
      </Campo>
      <Campo label="Nova senha" htmlFor="senha-nova" dica={DICA_SENHA}>
        <Input id="senha-nova" type="password" autoComplete="new-password" value={nova} onChange={(e) => setNova(e.target.value)} />
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
      <div className="flex justify-end gap-2">
        {aoCancelar && (
          <Button type="button" variant="outline" onClick={aoCancelar}>
            Cancelar
          </Button>
        )}
        <Button carregando={alterar.isPending} type="submit" disabled={!valido || alterar.isPending}>
          {alterar.isPending ? "Alterando..." : "Alterar senha"}
        </Button>
      </div>
    </form>
  )
}

/** Diálogo do menu do usuário. */
export function AlterarSenha({ aberto, aoFechar }: { aberto: boolean; aoFechar: () => void }) {
  return (
    <Dialog open={aberto} onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Alterar senha</DialogTitle>
          <DialogDescription>Você será desconectado de todos os dispositivos.</DialogDescription>
        </DialogHeader>
        <FormularioSenha aoCancelar={aoFechar} />
      </DialogContent>
    </Dialog>
  )
}

/** Tela única quando o admin definiu a senha: nada mais funciona até a pessoa escolher a própria. */
export function TrocaObrigatoria() {
  const { usuario, sair } = useAuth()
  return (
    <TelaDaMarca>
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-xl">Defina a sua senha</CardTitle>
          <CardDescription>
            Olá, {usuario?.nome}. A senha atual foi definida por um administrador. Escolha uma senha só sua para continuar.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <FormularioSenha />
          <Button variant="link" className="justify-self-center text-muted-foreground" onClick={() => sair()}>
            Sair
          </Button>
        </CardContent>
      </Card>
    </TelaDaMarca>
  )
}
