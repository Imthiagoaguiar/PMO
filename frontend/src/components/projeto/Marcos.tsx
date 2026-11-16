import { useMutation } from "@tanstack/react-query"
import { Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { Campo } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { api, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { CorMarco, FormaMarco, Marco } from "@/lib/tipos"

export const NOME_FORMA: Record<FormaMarco, string> = {
  bandeira: "bandeira",
  pin: "pin",
  circulo: "círculo",
  quadrado: "quadrado",
  triangulo: "triângulo",
  losango: "losango",
  estrela: "estrela",
  faixa: "período",
}
const FORMAS = Object.keys(NOME_FORMA) as FormaMarco[]

export const NOME_COR: Record<CorMarco, string> = {
  azul: "azul",
  vermelho: "vermelho",
  laranja: "laranja",
  amarelo: "amarelo",
  verde: "verde",
  roxo: "roxo",
  cinza: "cinza",
}
const CORES = Object.keys(NOME_COR) as CorMarco[]

/** Desenho da forma do marco (as cores vêm do index.css: --marco-*). */
export function SimboloMarco({ forma, cor, tamanho = 18 }: { forma: FormaMarco; cor: CorMarco; tamanho?: number }) {
  const preenchimento = `var(--marco-${cor})`
  const contorno = "var(--card)"
  let corpo
  switch (forma) {
    case "circulo":
      corpo = <circle cx="12" cy="12" r="7" />
      break
    case "quadrado":
      corpo = <rect x="5" y="5" width="14" height="14" rx="2" />
      break
    case "triangulo":
      corpo = <path d="M12 4 L20 19 L4 19 Z" strokeLinejoin="round" />
      break
    case "losango":
      corpo = <path d="M12 3 L21 12 L12 21 L3 12 Z" strokeLinejoin="round" />
      break
    case "estrela":
      corpo = <path d="M12 2.5l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 16.9 6.3 20l1.2-6.4L2.8 9.2l6.4-.8z" strokeLinejoin="round" />
      break
    case "pin":
      corpo = <path d="M12 22s-6.5-6.4-6.5-11.5a6.5 6.5 0 0 1 13 0C18.5 15.6 12 22 12 22zm0-9a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z" fillRule="evenodd" />
      break
    case "faixa":
      corpo = <rect x="2" y="8" width="20" height="8" rx="2" />
      break
    default: // bandeira
      corpo = (
        <>
          <path d="M6 3v18" stroke={preenchimento} strokeWidth="2" strokeLinecap="round" />
          <path d="M7 4h11l-3 4 3 4H7z" strokeLinejoin="round" />
        </>
      )
  }
  return (
    <svg width={tamanho} height={tamanho} viewBox="0 0 24 24" aria-hidden fill={preenchimento} stroke={contorno} strokeWidth="1.5"
      className="block shrink-0 drop-shadow-sm">
      {corpo}
    </svg>
  )
}

/** Criar (sem `marco`) ou editar um marco. */
export function MarcoDialog({ projetoId, marco, aoFechar }: { projetoId: number; marco?: Marco; aoFechar: () => void }) {
  const atualizar = useAtualizarProjeto(projetoId)
  const [titulo, setTitulo] = useState(marco?.titulo ?? "")
  const [descricao, setDescricao] = useState(marco?.descricao ?? "")
  const [data, setData] = useState(marco?.data ?? fmt.hojeISO())
  const [dataFim, setDataFim] = useState(marco?.data_fim ?? "")
  const [forma, setForma] = useState<FormaMarco>(marco?.forma ?? "bandeira")
  const [cor, setCor] = useState<CorMarco>(marco?.cor ?? "azul")
  const periodo = forma === "faixa"
  const fimInvalido = periodo && (!dataFim || dataFim < data)

  const aoErrar = (erro: unknown) => {
    fmt.notificarErro(erro)
    if (ehConflitoDeVersao(erro)) {
      atualizar()
      aoFechar()
    }
  }
  const salvar = useMutation({
    mutationFn: () => {
      const corpo = {
        titulo: titulo.trim(), descricao: descricao.trim() || null, data, forma, cor,
        data_fim: periodo ? dataFim : null,
      }
      return marco
        ? api<Marco>(`/projetos/${projetoId}/marcos/${marco.id}`, { method: "PATCH", json: { ...corpo, versao: marco.versao } })
        : api<Marco>(`/projetos/${projetoId}/marcos`, { method: "POST", json: corpo })
    },
    onSuccess: () => {
      atualizar()
      toast.success(marco ? "Marco atualizado." : "Marco adicionado à linha do tempo.")
      aoFechar()
    },
    onError: aoErrar,
  })
  const excluir = useMutation({
    mutationFn: () => api(`/projetos/${projetoId}/marcos/${marco!.id}`, { method: "DELETE" }),
    onSuccess: () => {
      atualizar()
      toast.success("Marco excluído.")
      aoFechar()
    },
    onError: aoErrar,
  })

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (titulo.trim() && data && !fimInvalido) salvar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{marco ? "Editar marco" : "Novo marco"}</DialogTitle>
            <DialogDescription>Um evento importante no meio do projeto: pré-entrega, go-live, auditoria, congelamento...</DialogDescription>
          </DialogHeader>

          <Campo label="Título" htmlFor="m-titulo">
            <Input id="m-titulo" required maxLength={80} autoFocus placeholder="Ex.: Pré-entrega" value={titulo}
              onChange={(e) => setTitulo(e.target.value)} />
          </Campo>

          <fieldset className="grid gap-1.5">
            <legend className="mb-1 text-sm font-medium">Forma</legend>
            <div className="flex flex-wrap gap-1.5">
              {FORMAS.map((f) => (
                <button
                  key={f} type="button" aria-pressed={forma === f} aria-label={NOME_FORMA[f]} title={NOME_FORMA[f]}
                  onClick={() => setForma(f)}
                  className={cn("flex size-10 items-center justify-center rounded-md border hover:bg-accent",
                    forma === f && "border-primary ring-2 ring-primary/30")}
                >
                  <SimboloMarco forma={f} cor={cor} tamanho={22} />
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="grid gap-1.5">
            <legend className="mb-1 text-sm font-medium">Cor</legend>
            <div className="flex flex-wrap gap-2">
              {CORES.map((c) => (
                <button
                  key={c} type="button" aria-pressed={cor === c} aria-label={NOME_COR[c]} title={NOME_COR[c]}
                  onClick={() => setCor(c)}
                  className={cn("size-7 rounded-full border-2 border-card ring-1 ring-border", cor === c && "ring-2 ring-foreground")}
                  style={{ background: `var(--marco-${c})` }}
                />
              ))}
            </div>
          </fieldset>

          <div className="grid grid-cols-2 gap-4">
            <Campo label={periodo ? "Início do período" : "Data"} htmlFor="m-data">
              <Input id="m-data" type="date" required value={data} onChange={(e) => setData(e.target.value)} />
            </Campo>
            {periodo && (
              <Campo label="Fim do período" htmlFor="m-fim">
                <Input id="m-fim" type="date" required aria-invalid={!!dataFim && dataFim < data} value={dataFim}
                  onChange={(e) => setDataFim(e.target.value)} />
              </Campo>
            )}
          </div>
          {periodo && !!dataFim && dataFim < data && (
            <p className="-mt-2 text-xs text-destructive">O fim não pode ser antes do início.</p>
          )}

          <Campo label="Descrição (opcional)" htmlFor="m-descricao">
            <Textarea id="m-descricao" rows={3} maxLength={500} value={descricao} onChange={(e) => setDescricao(e.target.value)} />
          </Campo>

          <DialogFooter className="sm:justify-between">
            {marco ? (
              <Button type="button" variant="ghost" className="text-destructive" disabled={excluir.isPending} onClick={() => excluir.mutate()}>
                <Trash2 /> Excluir
              </Button>
            ) : <span />}
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={aoFechar}>
                Cancelar
              </Button>
              <Button type="submit" disabled={salvar.isPending || !titulo.trim() || fimInvalido}>
                {salvar.isPending ? "Salvando..." : "Salvar"}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
