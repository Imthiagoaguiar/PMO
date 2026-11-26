import { useEffect, useRef, useState, type PointerEvent as EventoPonteiro } from "react"

// Cores validadas (CVD e contraste, claro e escuro) no index.css: realizado = azul, planejado = laranja tracejado.
const COR_REALIZADO = "var(--serie-realizado)"
const COR_PLANEJADO = "var(--serie-planejado)"

export interface PontoLinha {
  rotulo: string
  planejado: number | null
  realizado: number | null
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

/** Passo "redondo" para o eixo (10, 20, 25, 50...). */
function passoRedondo(valor: number): number {
  if (valor <= 0) return 1
  const ordem = 10 ** Math.floor(Math.log10(valor))
  return [1, 2, 2.5, 5, 10].map((m) => m * ordem).find((v) => v >= valor) ?? valor
}

/**
 * Duas linhas (planejado tracejado x realizado) com cursor e dica ao passar o mouse e a marca de "hoje".
 * `maximoFixo`: ex. 100 para porcentagem.
 */
export function GraficoLinhas({ pontos, rotuloAria, formatarEixo, formatarValor, indiceHoje, maximoFixo }: {
  pontos: PontoLinha[]
  rotuloAria: string
  formatarEixo: (v: number) => string
  formatarValor: (v: number) => string
  indiceHoje?: number
  maximoFixo?: number
}) {
  const [ref, largura] = useLargura<HTMLDivElement>()
  const [foco, setFoco] = useState<number | null>(null)
  const altura = 240
  const margem = { esq: 64, dir: 16, topo: 12, base: 26 }
  const valores = pontos.flatMap((p) => [p.planejado ?? 0, p.realizado ?? 0])
  const passo = passoRedondo((maximoFixo ?? Math.max(...valores, 1)) / 4)
  const maximo = maximoFixo ?? Math.max(passo * Math.ceil(Math.max(...valores, 1) / passo), passo)
  const w = Math.max(largura - margem.esq - margem.dir, 10)
  const h = altura - margem.topo - margem.base
  const x = (i: number) => margem.esq + (pontos.length === 1 ? w / 2 : (i / (pontos.length - 1)) * w)
  const y = (v: number) => margem.topo + h - (v / maximo) * h
  const caminho = (serie: (number | null)[]) =>
    serie.map((v, i) => (v === null ? "" : `${i === 0 || serie[i - 1] === null ? "M" : "L"}${x(i)},${y(v)}`)).join(" ")
  const ticks = Array.from({ length: Math.round(maximo / passo) + 1 }, (_, i) => i * passo)
  const passoRotulo = Math.max(1, Math.ceil(pontos.length / Math.max(1, Math.floor(w / 64))))
  const planejado = pontos.map((p) => p.planejado)
  const realizado = pontos.map((p) => p.realizado)

  function mover(e: EventoPonteiro<SVGRectElement>) {
    const caixa = e.currentTarget.getBoundingClientRect()
    setFoco(Math.min(pontos.length - 1, Math.max(0, Math.round(((e.clientX - caixa.left) / caixa.width) * (pontos.length - 1)))))
  }

  const ponto = foco !== null ? pontos[foco] : null
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="w-4 border-t-2 border-dashed" style={{ borderColor: COR_PLANEJADO }} /> Planejado</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: COR_REALIZADO }} /> Realizado</span>
      </div>
      <div ref={ref} className="relative">
        {largura > 0 && (
          <svg width={largura} height={altura} role="img" aria-label={rotuloAria}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={margem.esq} x2={margem.esq + w} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
                <text x={margem.esq - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted-foreground)">{formatarEixo(t)}</text>
              </g>
            ))}
            {pontos.map((p, i) => (i % passoRotulo === 0 || i === pontos.length - 1) && (
              <text key={p.rotulo + i} x={x(i)} y={altura - 6} fontSize={11} fill="var(--muted-foreground)"
                textAnchor={i === 0 ? "start" : i === pontos.length - 1 ? "end" : "middle"}>{p.rotulo}</text>
            ))}
            {indiceHoje !== undefined && indiceHoje >= 0 && (
              <g>
                <line x1={x(indiceHoje)} x2={x(indiceHoje)} y1={margem.topo} y2={margem.topo + h} stroke="var(--destructive)" strokeDasharray="3 3" strokeWidth={1.5} />
                <text x={x(indiceHoje) + 4} y={margem.topo + 10} fontSize={10} fill="var(--destructive)">hoje</text>
              </g>
            )}
            <path d={caminho(planejado)} fill="none" stroke={COR_PLANEJADO} strokeWidth={2} strokeDasharray="6 4" strokeLinejoin="round" />
            <path d={caminho(realizado)} fill="none" stroke={COR_REALIZADO} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {foco !== null && (
              <g pointerEvents="none">
                <line x1={x(foco)} x2={x(foco)} y1={margem.topo} y2={margem.topo + h} stroke="var(--muted-foreground)" strokeWidth={1} />
                {planejado[foco] !== null && <circle cx={x(foco)} cy={y(planejado[foco]!)} r={4.5} fill={COR_PLANEJADO} stroke="var(--card)" strokeWidth={2} />}
                {realizado[foco] !== null && <circle cx={x(foco)} cy={y(realizado[foco]!)} r={4.5} fill={COR_REALIZADO} stroke="var(--card)" strokeWidth={2} />}
              </g>
            )}
            <rect x={margem.esq} y={margem.topo} width={w} height={h} fill="transparent" onPointerMove={mover} onPointerLeave={() => setFoco(null)} />
          </svg>
        )}
        {ponto && foco !== null && (
          <div className="pointer-events-none absolute top-2 z-10 grid min-w-40 gap-0.5 rounded-md border bg-popover px-3 py-2 text-xs shadow-md"
            style={x(foco) > largura / 2 ? { right: largura - x(foco) + 12 } : { left: x(foco) + 12 }}>
            <p className="font-medium">{ponto.rotulo}</p>
            <p className="flex justify-between gap-3"><span>Planejado</span><span className="tabular-nums">{ponto.planejado === null ? "—" : formatarValor(ponto.planejado)}</span></p>
            <p className="flex justify-between gap-3"><span>Realizado</span><span className="tabular-nums">{ponto.realizado === null ? "—" : formatarValor(ponto.realizado)}</span></p>
          </div>
        )}
      </div>
    </div>
  )
}
