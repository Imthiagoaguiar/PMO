import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { PapelProjeto, PapelUsuario, StatusProjeto } from "@/lib/tipos"

const CORES_STATUS: Record<StatusProjeto, string> = {
  Planejado: "bg-slate-100 text-slate-700 border-slate-200",
  "Em andamento": "bg-blue-50 text-blue-700 border-blue-200",
  Concluído: "bg-emerald-50 text-emerald-700 border-emerald-200",
  Cancelado: "bg-red-50 text-red-700 border-red-200",
}

export function StatusBadge({ status }: { status: StatusProjeto }) {
  return (
    <Badge variant="outline" className={CORES_STATUS[status]}>
      {status}
    </Badge>
  )
}

export const NOMES_PAPEL_USUARIO: Record<PapelUsuario, string> = {
  admin: "Administrador",
  gerente: "Gerente",
  membro: "Membro",
}

export const NOMES_PAPEL_PROJETO: Record<PapelProjeto, string> = {
  editor: "Editor",
  visualizador: "Visualizador",
}

/** Cor da fase pela posição (index.css). Mais de 5 fases: as cores se repetem. */
export const COR_FASE = ["bg-fase-1", "bg-fase-2", "bg-fase-3", "bg-fase-4", "bg-fase-5"]
export function corFase(indice: number): string {
  return COR_FASE[indice % COR_FASE.length]
}

export function Carregando({ texto = "Carregando..." }: { texto?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" /> {texto}
    </div>
  )
}

export function Vazio({ titulo, children }: { titulo: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed px-6 py-10 text-center">
      <p className="font-medium">{titulo}</p>
      {children && <div className="mt-1 text-sm text-muted-foreground">{children}</div>}
    </div>
  )
}

export function ErroCarregar({ erro }: { erro: unknown }) {
  const mensagem = erro instanceof Error ? erro.message : "Não foi possível carregar."
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
      {mensagem}
    </div>
  )
}

export function Paginacao({
  total,
  limite,
  offset,
  aoMudar,
}: {
  total: number
  limite: number
  offset: number
  aoMudar: (offset: number) => void
}) {
  if (total <= limite) return null
  const pagina = Math.floor(offset / limite) + 1
  const paginas = Math.ceil(total / limite)
  return (
    <div className="flex items-center justify-end gap-2 pt-4 text-sm text-muted-foreground">
      <span>
        Página {pagina} de {paginas} · {total} itens
      </span>
      <Button variant="outline" size="icon-sm" disabled={offset === 0} onClick={() => aoMudar(Math.max(0, offset - limite))}>
        <ChevronLeft />
        <span className="sr-only">Anterior</span>
      </Button>
      <Button variant="outline" size="icon-sm" disabled={offset + limite >= total} onClick={() => aoMudar(offset + limite)}>
        <ChevronRight />
        <span className="sr-only">Próxima</span>
      </Button>
    </div>
  )
}

export function Cabecalho({ titulo, descricao, children }: { titulo: string; descricao?: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{titulo}</h1>
        {descricao && <p className="mt-1 text-sm text-muted-foreground">{descricao}</p>}
      </div>
      {children && <div className="flex flex-wrap gap-2">{children}</div>}
    </div>
  )
}

export function Campo({ label, htmlFor, dica, children, className }: {
  label: string
  htmlFor?: string
  dica?: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {dica && <p className="text-xs text-muted-foreground">{dica}</p>}
    </div>
  )
}

/** Mostra a barra de progresso com o número ao lado. */
export function BarraProgresso({ valor, className }: { valor: number; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className={cn("h-full rounded-full", valor >= 100 ? "bg-emerald-500" : "bg-primary")}
          style={{ width: `${Math.min(100, Math.max(0, valor))}%` }}
        />
      </div>
      <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">{Math.round(valor)}%</span>
    </div>
  )
}
