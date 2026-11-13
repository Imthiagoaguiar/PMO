import { useMutation } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { Campo } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { notificarErro } from "@/lib/formato"

const SENHA_MIN = 12

export function AlterarSenha({ aberto, aoFechar }: { aberto: boolean; aoFechar: () => void }) {
  const { encerrarLocalmente } = useAuth()
  const navigate = useNavigate()
  const [atual, setAtual] = useState("")
  const [nova, setNova] = useState("")
  const [confirmacao, setConfirmacao] = useState("")

  const alterar = useMutation({
    mutationFn: () => api("/auth/alterar-senha", { method: "POST", json: { senha_atual: atual, nova_senha: nova } }),
    onSuccess: () => {
      // O back revoga todas as sessões ao trocar a senha: entra de novo com a nova.
      encerrarLocalmente()
      toast.success("Senha alterada. Entre novamente com a nova senha.")
      navigate("/login")
    },
    onError: notificarErro,
  })

  const erroConfirmacao = confirmacao.length > 0 && confirmacao !== nova
  const valido = atual.length > 0 && nova.length >= SENHA_MIN && nova === confirmacao

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (valido) alterar.mutate()
  }

  return (
    <Dialog open={aberto} onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Alterar senha</DialogTitle>
            <DialogDescription>Você será desconectado de todos os dispositivos.</DialogDescription>
          </DialogHeader>
          <Campo label="Senha atual" htmlFor="senha-atual">
            <Input id="senha-atual" type="password" autoComplete="current-password" value={atual} onChange={(e) => setAtual(e.target.value)} />
          </Campo>
          <Campo label="Nova senha" htmlFor="senha-nova" dica={`Mínimo de ${SENHA_MIN} caracteres. Uma frase longa é mais segura que símbolos.`}>
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
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>
              Cancelar
            </Button>
            <Button type="submit" disabled={!valido || alterar.isPending}>
              {alterar.isPending ? "Alterando..." : "Alterar senha"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
