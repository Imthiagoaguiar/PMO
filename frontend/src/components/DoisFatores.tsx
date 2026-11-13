import { useMutation, useQuery } from "@tanstack/react-query"
import { Copy, Download, ShieldCheck, ShieldOff } from "lucide-react"
import QRCode from "qrcode"
import { useEffect, useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { Campo, Carregando, ErroCarregar } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { notificarErro } from "@/lib/formato"
import type { StatusDoisFatores } from "@/lib/tipos"

// ---------- Peças ----------

function CampoCodigo({ valor, aoMudar, id = "codigo-2fa" }: { valor: string; aoMudar: (v: string) => void; id?: string }) {
  return (
    <Campo label="Código do app autenticador" htmlFor={id}>
      <Input
        id={id}
        inputMode="numeric"
        autoComplete="one-time-code"
        placeholder="000000"
        maxLength={6}
        className="w-40 text-center font-mono text-lg tracking-[0.4em]"
        value={valor}
        onChange={(e) => aoMudar(e.target.value.replace(/\D/g, ""))}
      />
    </Campo>
  )
}

/** Mostra os códigos de recuperação uma única vez, com copiar e baixar. */
function CodigosRecuperacao({ codigos, aoConcluir, rotuloConcluir }: { codigos: string[]; aoConcluir: () => void; rotuloConcluir: string }) {
  const [guardou, setGuardou] = useState(false)
  const texto = `Códigos de recuperação do PMO Waterfall\nCada código vale uma única vez.\n\n${codigos.join("\n")}\n`

  function baixar() {
    const url = URL.createObjectURL(new Blob([texto], { type: "text/plain" }))
    const link = Object.assign(document.createElement("a"), { href: url, download: "pmo-codigos-de-recuperacao.txt" })
    link.click()
    URL.revokeObjectURL(url)
    setGuardou(true)
  }

  async function copiar() {
    await navigator.clipboard.writeText(texto)
    toast.success("Códigos copiados.")
    setGuardou(true)
  }

  return (
    <div className="grid gap-4">
      <p className="text-sm">
        Guarde estes códigos em lugar seguro (gerenciador de senhas ou papel). Se perder o celular, cada um permite entrar
        <strong> uma vez</strong>. <strong>Eles não serão mostrados de novo.</strong>
      </p>
      <ol className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/40 p-4 font-mono text-sm">
        {codigos.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={copiar}>
          <Copy /> Copiar
        </Button>
        <Button type="button" variant="outline" onClick={baixar}>
          <Download /> Baixar .txt
        </Button>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={guardou} onChange={(e) => setGuardou(e.target.checked)} className="size-4" />
        Guardei os códigos em lugar seguro
      </label>
      <Button onClick={aoConcluir} disabled={!guardou}>
        {rotuloConcluir}
      </Button>
    </div>
  )
}

// ---------- Ativação (senha -> QR code -> código -> códigos de recuperação) ----------

function AtivarDoisFatores() {
  const { encerrarLocalmente } = useAuth()
  const navigate = useNavigate()
  const [senha, setSenha] = useState("")
  const [configuracao, setConfiguracao] = useState<{ segredo: string; uri: string } | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [codigo, setCodigo] = useState("")
  const [codigos, setCodigos] = useState<string[] | null>(null)

  useEffect(() => {
    // O QR é gerado aqui no navegador: o segredo não passa por nenhum serviço externo.
    if (configuracao) QRCode.toDataURL(configuracao.uri, { width: 200, margin: 1 }).then(setQr)
  }, [configuracao])

  const iniciar = useMutation({
    mutationFn: () => api<{ segredo: string; uri: string }>("/auth/2fa/iniciar", { method: "POST", json: { senha } }),
    onSuccess: (c) => {
      setSenha("")
      setConfiguracao(c)
    },
    onError: notificarErro,
  })

  const ativar = useMutation({
    mutationFn: () => api<{ codigos_recuperacao: string[] }>("/auth/2fa/ativar", { method: "POST", json: { codigo } }),
    onSuccess: (r) => setCodigos(r.codigos_recuperacao),
    onError: (erro) => {
      setCodigo("")
      notificarErro(erro)
    },
  })

  function concluir() {
    // Ativar encerra todas as sessões: a próxima entrada já pede o código.
    encerrarLocalmente()
    toast.success("Verificação em duas etapas ativada. Entre novamente.")
    navigate("/login")
  }

  if (codigos) return <CodigosRecuperacao codigos={codigos} aoConcluir={concluir} rotuloConcluir="Concluir e entrar novamente" />

  if (!configuracao) {
    return (
      <form
        className="grid gap-4"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          iniciar.mutate()
        }}
      >
        <p className="text-sm text-muted-foreground">
          Você vai precisar de um app autenticador no celular, como Google Authenticator, Microsoft Authenticator ou 1Password.
        </p>
        <Campo label="Confirme sua senha" htmlFor="senha-2fa">
          <Input id="senha-2fa" type="password" autoComplete="current-password" autoFocus value={senha} onChange={(e) => setSenha(e.target.value)} />
        </Campo>
        <Button type="submit" disabled={!senha || iniciar.isPending} className="justify-self-start">
          {iniciar.isPending ? "Gerando..." : "Continuar"}
        </Button>
      </form>
    )
  }

  return (
    <form
      className="grid gap-4"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        ativar.mutate()
      }}
    >
      <ol className="grid list-decimal gap-1 pl-5 text-sm">
        <li>No app autenticador, adicione uma conta lendo o QR code abaixo.</li>
        <li>Digite o código de 6 dígitos que o app mostrar.</li>
      </ol>
      <div className="flex flex-wrap items-center gap-6">
        <div className="flex size-[200px] items-center justify-center rounded-lg border bg-white">
          {qr ? <img src={qr} alt="QR code para o app autenticador" width={200} height={200} /> : <Carregando texto="" />}
        </div>
        <div className="grid min-w-0 flex-1 gap-1 text-sm">
          <span className="text-muted-foreground">Não consegue ler? Digite esta chave no app:</span>
          <code className="rounded bg-muted px-2 py-1 font-mono text-xs break-all select-all">
            {configuracao.segredo.match(/.{1,4}/g)?.join(" ")}
          </code>
        </div>
      </div>
      <CampoCodigo valor={codigo} aoMudar={setCodigo} />
      <Button type="submit" disabled={codigo.length !== 6 || ativar.isPending} className="justify-self-start">
        {ativar.isPending ? "Verificando..." : "Ativar"}
      </Button>
    </form>
  )
}

// ---------- Já ativo: novos códigos / desativar ----------

function GerenciarAtivo({ status, aoAlterar }: { status: StatusDoisFatores; aoAlterar: () => void }) {
  const [acao, setAcao] = useState<"codigos" | "desativar" | null>(null)
  const [senha, setSenha] = useState("")
  const [codigo, setCodigo] = useState("")
  const [codigos, setCodigos] = useState<string[] | null>(null)

  function limpar() {
    setSenha("")
    setCodigo("")
  }
  function falhou(erro: unknown) {
    setCodigo("")
    notificarErro(erro)
  }

  const gerarCodigos = useMutation({
    mutationFn: () => api<{ codigos_recuperacao: string[] }>("/auth/2fa/codigos", { method: "POST", json: { senha, codigo } }),
    onSuccess: (r) => {
      limpar()
      setCodigos(r.codigos_recuperacao)
    },
    onError: falhou,
  })

  const desativar = useMutation({
    mutationFn: () => api("/auth/2fa/desativar", { method: "POST", json: { senha, codigo } }),
    onSuccess: () => {
      limpar()
      toast.success("Verificação em duas etapas desativada.")
      setAcao(null)
      aoAlterar()
    },
    onError: falhou,
  })
  const executar = acao === "codigos" ? gerarCodigos : desativar

  if (codigos) {
    return (
      <CodigosRecuperacao
        codigos={codigos}
        rotuloConcluir="Pronto"
        aoConcluir={() => {
          setCodigos(null)
          setAcao(null)
          aoAlterar()
        }}
      />
    )
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        <ShieldCheck className="size-5 shrink-0" />
        <div>
          <p className="font-medium">Ativa</p>
          <p>
            {status.codigos_restantes} código(s) de recuperação disponíve{status.codigos_restantes === 1 ? "l" : "is"}.
            {status.codigos_restantes <= 3 && " Gere novos códigos."}
          </p>
        </div>
      </div>

      {!acao ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setAcao("codigos")}>
            Gerar novos códigos de recuperação
          </Button>
          {!status.obrigatorio && (
            <Button variant="outline" className="text-destructive" onClick={() => setAcao("desativar")}>
              <ShieldOff /> Desativar
            </Button>
          )}
        </div>
      ) : (
        <form
          className="grid gap-4 rounded-lg border p-4"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            executar.mutate()
          }}
        >
          <p className="text-sm font-medium">
            {acao === "codigos" ? "Os códigos atuais deixarão de valer." : "Sua conta ficará protegida só pela senha."} Confirme:
          </p>
          <Campo label="Senha" htmlFor="senha-gerenciar-2fa">
            <Input id="senha-gerenciar-2fa" type="password" autoComplete="current-password" autoFocus value={senha} onChange={(e) => setSenha(e.target.value)} />
          </Campo>
          <CampoCodigo id="codigo-gerenciar-2fa" valor={codigo} aoMudar={setCodigo} />
          <div className="flex gap-2">
            <Button type="submit" variant={acao === "desativar" ? "destructive" : "default"} disabled={!senha || codigo.length !== 6 || executar.isPending}>
              {acao === "codigos" ? "Gerar códigos" : "Desativar"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setAcao(null)}>
              Cancelar
            </Button>
          </div>
        </form>
      )}
      {status.obrigatorio && <p className="text-xs text-muted-foreground">Obrigatória para administradores: não pode ser desativada.</p>}
    </div>
  )
}

// ---------- Entradas ----------

/** Diálogo do menu do usuário. */
export function DoisFatoresDialog({ aberto, aoFechar }: { aberto: boolean; aoFechar: () => void }) {
  const status = useQuery({
    queryKey: ["2fa"],
    queryFn: () => api<StatusDoisFatores>("/auth/2fa"),
    enabled: aberto,
  })

  return (
    <Dialog open={aberto} onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Verificação em duas etapas</DialogTitle>
          <DialogDescription>Além da senha, o login pede um código que muda a cada 30 segundos no seu celular.</DialogDescription>
        </DialogHeader>
        {status.isPending ? (
          <Carregando />
        ) : status.isError ? (
          <ErroCarregar erro={status.error} />
        ) : status.data.ativo ? (
          <GerenciarAtivo status={status.data} aoAlterar={() => status.refetch()} />
        ) : (
          <AtivarDoisFatores />
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Tela única para admin sem 2FA: nada mais funciona até ativar. */
export function DoisFatoresObrigatorio() {
  const { usuario, sair } = useAuth()
  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/40 px-4 py-8">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <ShieldCheck className="size-5" />
          </div>
          <CardTitle className="text-xl">Ative a verificação em duas etapas</CardTitle>
          <CardDescription>
            Olá, {usuario?.nome}. Contas de administrador precisam de um segundo fator: além da senha, o login vai pedir um código
            do seu celular.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <AtivarDoisFatores />
          <Button variant="link" className="justify-self-center text-muted-foreground" onClick={() => sair()}>
            Sair
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
