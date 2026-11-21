import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"

import { api } from "@/lib/api"
import type { Dependencia, Gargalos, Indicadores, Marco, Membro, Pilar, Projeto, ResumoOrcamento } from "@/lib/tipos"

export const chaves = {
  projeto: (id: number) => ["projeto", id] as const,
  indicadores: (id: number) => ["projeto", id, "indicadores"] as const,
  membros: (id: number) => ["projeto", id, "membros"] as const,
  pilares: (id: number) => ["projeto", id, "pilares"] as const,
  dependencias: (id: number) => ["projeto", id, "dependencias"] as const,
  marcos: (id: number) => ["projeto", id, "marcos"] as const,
  orcamento: (id: number) => ["projeto", id, "orcamento"] as const,
  gargalos: (id: number) => ["projeto", id, "gargalos"] as const,
  comentarios: (id: number, filtro: string) => ["projeto", id, "comentarios", filtro] as const,
  historico: (id: number, offset: number) => ["projeto", id, "historico", offset] as const,
}

export function useProjeto(id: number) {
  return useQuery({ queryKey: chaves.projeto(id), queryFn: () => api<Projeto>(`/projetos/${id}`) })
}

export function useIndicadores(id: number) {
  return useQuery({ queryKey: chaves.indicadores(id), queryFn: () => api<Indicadores>(`/projetos/${id}/indicadores`) })
}

export function useMembros(id: number) {
  return useQuery({ queryKey: chaves.membros(id), queryFn: () => api<Membro[]>(`/projetos/${id}/membros`) })
}

export function usePilares(id: number) {
  return useQuery({ queryKey: chaves.pilares(id), queryFn: () => api<Pilar[]>(`/projetos/${id}/pilares`) })
}

export function useDependencias(id: number) {
  return useQuery({ queryKey: chaves.dependencias(id), queryFn: () => api<Dependencia[]>(`/projetos/${id}/dependencias`) })
}

export function useMarcos(id: number) {
  return useQuery({ queryKey: chaves.marcos(id), queryFn: () => api<Marco[]>(`/projetos/${id}/marcos`) })
}

export function useOrcamento(id: number) {
  return useQuery({ queryKey: chaves.orcamento(id), queryFn: () => api<ResumoOrcamento>(`/projetos/${id}/orcamento`) })
}

export function useGargalos(id: number) {
  return useQuery({ queryKey: chaves.gargalos(id), queryFn: () => api<Gargalos>(`/projetos/${id}/gargalos`) })
}

/** Depois de qualquer alteração: recarrega projeto, indicadores, membros e histórico. */
export function useAtualizarProjeto(id: number) {
  const queryClient = useQueryClient()
  return useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["projeto", id] })
    queryClient.invalidateQueries({ queryKey: ["projetos"] })
  }, [queryClient, id])
}
