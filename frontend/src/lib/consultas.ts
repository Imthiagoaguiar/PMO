import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"

import { api } from "@/lib/api"
import type { Indicadores, Membro, Projeto } from "@/lib/tipos"

export const chaves = {
  projeto: (id: number) => ["projeto", id] as const,
  indicadores: (id: number) => ["projeto", id, "indicadores"] as const,
  membros: (id: number) => ["projeto", id, "membros"] as const,
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

/** Depois de qualquer alteração: recarrega projeto, indicadores, membros e histórico. */
export function useAtualizarProjeto(id: number) {
  const queryClient = useQueryClient()
  return useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["projeto", id] })
    queryClient.invalidateQueries({ queryKey: ["projetos"] })
  }, [queryClient, id])
}
