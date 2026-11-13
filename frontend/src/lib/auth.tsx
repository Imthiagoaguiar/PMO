import { useQueryClient } from "@tanstack/react-query"
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"

import * as cliente from "@/lib/api"
import type { Usuario } from "@/lib/tipos"

interface Auth {
  usuario: Usuario | null
  carregando: boolean
  /** Retorna o desafio quando a conta usa 2FA (falta o código). */
  entrar: (email: string, senha: string) => Promise<cliente.ResultadoLogin>
  entrarComCodigo: (desafio: string, codigo: string) => Promise<void>
  sair: () => Promise<void>
  /** Limpa a sessão sem chamar a API (ex.: depois de trocar a senha, o back já revogou tudo). */
  encerrarLocalmente: () => void
}

const AuthContext = createContext<Auth | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [usuario, setUsuario] = useState<Usuario | null>(null)
  const [carregando, setCarregando] = useState(true)

  const encerrarLocalmente = useCallback(() => {
    cliente.esquecerSessaoLocal()
    setUsuario(null)
    queryClient.clear() // não deixa dados do usuário anterior no cache
  }, [queryClient])

  useEffect(() => {
    cliente.definirAoExpirarSessao(encerrarLocalmente)

    // Ao abrir/recarregar a página: o access token sumiu da memória, mas o cookie de
    // refresh pode continuar válido. Tenta recuperar a sessão sem pedir login.
    let ativo = true
    ;(async () => {
      try {
        if ((await cliente.renovarSessao()) === "ok") {
          const eu = await cliente.api<Usuario>("/auth/me")
          if (ativo) setUsuario(eu)
        }
      } catch {
        // sem sessão: segue para o login
      } finally {
        if (ativo) setCarregando(false)
      }
    })()
    return () => {
      ativo = false
    }
  }, [encerrarLocalmente])

  const entrar = useCallback(async (email: string, senha: string) => {
    const resultado = await cliente.entrar(email, senha)
    if (!resultado.requer2fa) setUsuario(await cliente.api<Usuario>("/auth/me"))
    return resultado
  }, [])

  const entrarComCodigo = useCallback(async (desafio: string, codigo: string) => {
    await cliente.entrarComCodigo(desafio, codigo)
    setUsuario(await cliente.api<Usuario>("/auth/me"))
  }, [])

  const sair = useCallback(async () => {
    try {
      await cliente.sair()
    } finally {
      encerrarLocalmente()
    }
  }, [encerrarLocalmente])

  const valor = useMemo(
    () => ({ usuario, carregando, entrar, entrarComCodigo, sair, encerrarLocalmente }),
    [usuario, carregando, entrar, entrarComCodigo, sair, encerrarLocalmente],
  )
  return <AuthContext.Provider value={valor}>{children}</AuthContext.Provider>
}

export function useAuth(): Auth {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth precisa estar dentro de <AuthProvider>")
  return ctx
}

/** O usuário logado (para telas que só aparecem com sessão). */
export function useUsuario(): Usuario {
  const { usuario } = useAuth()
  if (!usuario) throw new Error("useUsuario usado sem sessão")
  return usuario
}

export function podeCriarProjeto(usuario: Usuario) {
  return usuario.papel === "admin" || usuario.papel === "gerente"
}
