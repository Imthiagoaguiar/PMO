import { useEffect, useRef, useState, type PointerEvent as EventoPonteiro } from "react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import * as fmt from "@/lib/formato"
import type { PontoCurva, TotalOrcamento } from "@/lib/tipos"

// Cores validadas (CVD e contraste, claro e escuro) no index.css: realizado = azul, orçado/planejado = laranja.
const COR_REALIZADO = "var(--serie-realizado)"
const COR_PLANEJADO = "var(--serie-planejado)"
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

/** "R$ 1,2 mi", "R$ 350 mil": rótulos de eixo curtos (a dica mostra o valor exato). */
export function reaisCurto(valor: number): string {
  const abs = Math.abs(valor)
  const [div, suf] = abs >= 1e9 ? [1e9, " bi"] : abs >= 1e6 ? [1e6, " mi"] : abs >= 1e3 ? [1e3, " mil"] : [1, ""]
  const n = valor / div
  return `R$ ${n.toLocaleString("pt-BR", { maximumFractionDigits: Math.abs(n) < 10 && div > 1 ? 1 : 0 })}${suf}`
}

function mesCurto(mes: string) {
  return `${MESES[Number(mes.slice(5, 7)) - 1]}/${mes.slice(2, 4)}`
}

function teto(valor: number): number {
  if (valor <= 0) return 1
  const ordem = 10 ** Math.floor(Math.log10(valor))
  return [1, 2, 2.5, 5, 10].map((m) => m * ordem).find((v) => v >= valor) ?? valor
}

function useLargura<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [largura, setLargura] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const obs = new ResizeObserver(([e]) => setLargura(e.contentRect.width))
    obs.observe(ref.current)
    return () => obs.disconnect()
  }, [])
  return [ref, largura] as const
}

function Legenda({ planejado }: { planejado: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
      <span className="flex items-center gap-1.5">
        <span className="h-0.5 w-4 rounded" style={{ background: COR_REALIZADO }} /> Realizado
      </span>
      {planejado && (
        <span className="flex items-center gap-1.5">
          <span className="w-4 border-t-2 border-dashed" style={{ borderColor: COR_PLANEJADO }} /> Planejado (orçado distribuído no período)
        </span>
      )}
    </div>
  )
}

/** Realizado x planejado acumulados, mês a mês. Passe o mouse para ver os valores do mês. */
export function CurvaAcumulada({ curva }: { curva: PontoCurva[] }) {
  const [ref, largura] = useLargura<HTMLDivElement>()
  const [foco, setFoco] = useState<number | null>(null)
  const altura = 220
  const margem = { esq: 64, dir: 16, topo: 12, base: 24 }
  const comPlano = curva.some((p) => p.planejado_acumulado !== null)
  const real = curva.map((p) => Number(p.realizado_acumulado))
  const plano = curva.map((p) => (p.planejado_acumulado === null ? null : Number(p.planejado_acumulado)))
  // Eixo com passo "redondo" (10 mil, 25 mil, 50 mil...) e 3 a 5 divisões.
  const passoEixo = teto(Math.max(...real, ...plano.map((v) => v ?? 0)) / 4)
  const maximo = Math.max(passoEixo * Math.ceil(Math.max(...real, ...plano.map((v) => v ?? 0), 1) / passoEixo), passoEixo)
  const w = Math.max(largura - margem.esq - margem.dir, 10)
  const h = altura - margem.topo - margem.base
  const x = (i: number) => margem.esq + (curva.length === 1 ? w / 2 : (i / (curva.length - 1)) * w)
  const y = (v: number) => margem.topo + h - (v / maximo) * h
  const caminho = (valores: (number | null)[]) =>
    valores.map((v, i) => (v === null ? "" : `${i === 0 || valores[i - 1] === null ? "M" : "L"}${x(i)},${y(v)}`)).join(" ")
  const passo = Math.max(1, Math.ceil(curva.length / Math.max(1, Math.floor(w / 56))))
  const ticks = Array.from({ length: Math.round(maximo / passoEixo) + 1 }, (_, i) => i * passoEixo)

  function mover(e: EventoPonteiro<SVGRectElement>) {
    const caixa = e.currentTarget.getBoundingClientRect()
    const rel = (e.clientX - caixa.left) / caixa.width
    setFoco(Math.min(curva.length - 1, Math.max(0, Math.round(rel * (curva.length - 1)))))
  }

  const ponto = foco !== null ? curva[foco] : null
  return (
    <div className="grid gap-2">
      <Legenda planejado={comPlano} />
      <div ref={ref} className="relative">
        {largura > 0 && (
          <svg width={largura} height={altura} role="img" aria-label="Gráfico: realizado acumulado e planejado acumulado por mês">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={margem.esq} x2={margem.esq + w} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
                <text x={margem.esq - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted-foreground)">
                  {reaisCurto(t)}
                </text>
              </g>
            ))}
            {curva.map((p, i) => i % passo === 0 && (
              <text key={p.mes} x={x(i)} y={altura - 6} fontSize={11} fill="var(--muted-foreground)"
                textAnchor={curva.length > 1 && i === 0 ? "start" : curva.length > 1 && i === curva.length - 1 ? "end" : "middle"}>
                {mesCurto(p.mes)}
              </text>
            ))}
            {comPlano && (
              <path d={caminho(plano)} fill="none" stroke={COR_PLANEJADO} strokeWidth={2} strokeDasharray="6 4" strokeLinejoin="round" />
            )}
            <path d={caminho(real)} fill="none" stroke={COR_REALIZADO} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {foco !== null && (
              <g pointerEvents="none">
                <line x1={x(foco)} x2={x(foco)} y1={margem.topo} y2={margem.topo + h} stroke="var(--muted-foreground)" strokeWidth={1} />
                {plano[foco] !== null && (
                  <circle cx={x(foco)} cy={y(plano[foco]!)} r={4.5} fill={COR_PLANEJADO} stroke="var(--card)" strokeWidth={2} />
                )}
                <circle cx={x(foco)} cy={y(real[foco])} r={4.5} fill={COR_REALIZADO} stroke="var(--card)" strokeWidth={2} />
              </g>
            )}
            <rect x={margem.esq} y={margem.topo} width={w} height={h} fill="transparent"
              onPointerMove={mover} onPointerLeave={() => setFoco(null)} />
          </svg>
        )}
        {ponto && foco !== null && (
          <div
            className="pointer-events-none absolute top-2 z-10 grid min-w-44 gap-0.5 rounded-md border bg-popover px-3 py-2 text-xs shadow-md"
            style={x(foco) > largura / 2 ? { right: largura - x(foco) + 12 } : { left: x(foco) + 12 }}
          >
            <p className="font-medium">{mesCurto(ponto.mes)}</p>
            <p className="flex justify-between gap-3"><span>Realizado acumulado</span><span className="tabular-nums">{fmt.moeda(ponto.realizado_acumulado)}</span></p>
            {ponto.planejado_acumulado !== null && (
              <p className="flex justify-between gap-3"><span>Planejado acumulado</span><span className="tabular-nums">{fmt.moeda(ponto.planejado_acumulado)}</span></p>
            )}
            <p className="flex justify-between gap-3 text-muted-foreground"><span>Gasto no mês</span><span className="tabular-nums">{fmt.moeda(ponto.realizado_no_mes)}</span></p>
          </div>
        )}
      </div>
    </div>
  )
}

const MAX_BARRAS = 12

/** Orçado x realizado por grupo (item, categoria ou pilar). Acima de 12 grupos, o resto vira "Outros". */
export function BarrasOrcadoRealizado({ grupos }: { grupos: TotalOrcamento[] }) {
  const ordenados = [...grupos].sort((a, b) => Number(b.orcado) - Number(a.orcado))
  const visiveis = ordenados.slice(0, MAX_BARRAS)
  const resto = ordenados.slice(MAX_BARRAS)
  if (resto.length) {
    visiveis.push({
      chave: "outros", nome: `Outros (${resto.length})`,
      orcado: String(resto.reduce((s, g) => s + Number(g.orcado), 0)),
      realizado: String(resto.reduce((s, g) => s + Number(g.realizado), 0)),
    })
  }
  const maximo = teto(Math.max(...visiveis.flatMap((g) => [Number(g.orcado), Number(g.realizado)]), 0))
  const pct = (v: string) => `${Math.max((Number(v) / maximo) * 100, 0)}%`

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: COR_PLANEJADO }} /> Orçado</span>
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: COR_REALIZADO }} /> Realizado</span>
      </div>
      <ul className="grid gap-2.5">
        {visiveis.map((g) => {
          const estourou = Number(g.realizado) > Number(g.orcado)
          return (
            <Tooltip key={g.chave}>
              <TooltipTrigger asChild>
                <li tabIndex={0} className="grid grid-cols-[minmax(6rem,12rem)_1fr_auto] items-center gap-3 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span className="truncate text-sm" title={g.nome}>{g.nome}</span>
                  <span className="grid gap-0.5">
                    <span className="block h-2 rounded-r" style={{ width: pct(g.orcado), background: COR_PLANEJADO, minWidth: Number(g.orcado) ? 2 : 0 }} />
                    <span className="block h-2 rounded-r" style={{ width: pct(g.realizado), background: COR_REALIZADO, minWidth: Number(g.realizado) ? 2 : 0 }} />
                  </span>
                  <span className={estourou ? "text-xs font-medium text-destructive tabular-nums" : "text-xs text-muted-foreground tabular-nums"}>
                    {Number(g.orcado) ? `${Math.round((Number(g.realizado) / Number(g.orcado)) * 100)}%` : "—"}
                  </span>
                </li>
              </TooltipTrigger>
              <TooltipContent>
                <p className="font-medium">{g.nome}</p>
                <p>Orçado: {fmt.moeda(g.orcado)}</p>
                <p>Realizado: {fmt.moeda(g.realizado)}</p>
                <p>Saldo: {fmt.moeda(Number(g.orcado) - Number(g.realizado))}</p>
              </TooltipContent>
            </Tooltip>
          )
        })}
      </ul>
      <div className="grid grid-cols-[minmax(6rem,12rem)_1fr_auto] gap-3 text-[11px] text-muted-foreground">
        <span />
        <span className="flex justify-between"><span>R$ 0</span><span>{reaisCurto(maximo)}</span></span>
        <span className="invisible">100%</span>
      </div>
    </div>
  )
}
