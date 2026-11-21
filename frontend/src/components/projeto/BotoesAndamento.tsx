import { cn } from "@/lib/utils"

export const PASSOS_ANDAMENTO = [0, 25, 50, 75, 100] as const

/** Botões 0/25/50/75/100% (combinado com o usuário em 20/11/2026); o valor exato continua possível no campo ao lado. */
export function BotoesAndamento({ valor, aoEscolher, desabilitado = false, tamanho = "normal" }: {
  valor: number
  aoEscolher: (v: number) => void
  desabilitado?: boolean
  tamanho?: "normal" | "compacto"
}) {
  return (
    <div role="group" aria-label="Andamento" className="flex flex-wrap gap-1">
      {PASSOS_ANDAMENTO.map((passo) => (
        <button
          key={passo}
          type="button"
          aria-pressed={valor === passo}
          disabled={desabilitado}
          onClick={() => aoEscolher(passo)}
          className={cn(
            "rounded-md border font-medium tabular-nums transition-colors hover:bg-accent disabled:opacity-50",
            tamanho === "compacto" ? "h-7 min-w-11 px-2 text-xs" : "h-9 min-w-14 px-3 text-sm",
            valor === passo && (passo === 100 ? "border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-600" : "border-primary bg-primary text-primary-foreground hover:bg-primary"),
          )}
        >
          {passo}%
        </button>
      ))}
    </div>
  )
}
