import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { PanelLeft, Search } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"

import { BarraProgresso, StatusBadge } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { Pagina, ProjetoResumo } from "@/lib/tipos"

/**
 * Painel lateral para trocar de projeto sem voltar à lista: busca, status e andamento de cada um.
 * Abre o outro projeto na MESMA aba (ex.: Orçamento), pelo ?aba= da URL.
 */
export function TrocarProjeto({ atual, aba }: { atual: number; aba: string }) {
  const [aberto, setAberto] = useState(false)
  const [busca, setBusca] = useState("")
  const parametros = new URLSearchParams({ limite: "100" })
  if (busca.trim()) parametros.set("busca", busca.trim())
  const lista = useQuery({
    queryKey: ["projetos", "painel", parametros.toString()],
    queryFn: () => api<Pagina<ProjetoResumo>>(`/projetos?${parametros}`),
    enabled: aberto,
    placeholderData: keepPreviousData,
  })

  return (
    <Dialog open={aberto} onOpenChange={setAberto}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <PanelLeft /> Trocar de projeto
        </Button>
      </DialogTrigger>
      <DialogContent
        className="top-0 left-0 flex h-svh max-w-sm translate-x-0 translate-y-0 flex-col gap-3 rounded-none border-y-0 border-l-0 p-4 data-[state=closed]:zoom-out-100 data-[state=open]:zoom-in-100 data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left sm:max-w-sm"
      >
        <DialogHeader>
          <DialogTitle>Projetos</DialogTitle>
          <DialogDescription>Escolha outro projeto: ele abre na mesma aba em que você está.</DialogDescription>
        </DialogHeader>
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input aria-label="Buscar projeto" placeholder="Buscar pelo nome" className="pl-8" value={busca} onChange={(e) => setBusca(e.target.value)} autoFocus />
        </div>
        <nav aria-label="Lista de projetos" className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1">
          {lista.isPending ? (
            <p className="py-4 text-sm text-muted-foreground">Carregando...</p>
          ) : lista.isError ? (
            <p className="py-4 text-sm text-destructive">Não foi possível carregar os projetos.</p>
          ) : lista.data.itens.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">Nenhum projeto {busca.trim() ? "com esse nome" : ""}.</p>
          ) : (
            <ul className="grid gap-1">
              {lista.data.itens.map((p) => {
                const ehAtual = p.id === atual
                return (
                  <li key={p.id}>
                    <Link
                      to={`/projetos/${p.id}?aba=${encodeURIComponent(aba)}`}
                      aria-current={ehAtual ? "page" : undefined}
                      onClick={() => setAberto(false)}
                      className={cn(
                        "grid gap-1.5 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-accent",
                        ehAtual ? "border-foreground/20 bg-accent shadow-[inset_3px_0_0_var(--marca)]" : "border-transparent",
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate font-medium">{p.nome_projeto}</span>
                        <StatusBadge status={p.status} />
                      </span>
                      <BarraProgresso valor={p.percentual_conclusao ?? 0} />
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
          {lista.data && lista.data.total > lista.data.itens.length && (
            <p className="px-1 pt-2 text-xs text-muted-foreground">
              Mostrando {lista.data.itens.length} de {lista.data.total}. Use a busca para achar os outros.
            </p>
          )}
        </nav>
      </DialogContent>
    </Dialog>
  )
}
