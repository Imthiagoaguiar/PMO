import type { ReactNode } from "react"
import { createBrowserRouter, Navigate, RouterProvider, useLocation } from "react-router"

import { TrocaObrigatoria } from "@/components/AlterarSenha"
import { Carregando } from "@/components/comum"
import { Layout } from "@/components/Layout"
import { useAuth } from "@/lib/auth"
import type { PapelUsuario } from "@/lib/tipos"
import { Auditoria } from "@/pages/Auditoria"
import { Login } from "@/pages/Login"
import { MinhasTarefas } from "@/pages/MinhasTarefas"
import { ProjetoDetalhe } from "@/pages/ProjetoDetalhe"
import { Projetos } from "@/pages/Projetos"
import { Usuarios } from "@/pages/Usuarios"

/** Só renderiza com sessão; senão manda para o login lembrando de onde veio. */
function ComSessao({ children }: { children: ReactNode }) {
  const { usuario, carregando } = useAuth()
  const location = useLocation()
  if (carregando) return <Carregando texto="Verificando sua sessão..." />
  if (!usuario) return <Navigate to="/login" replace state={{ de: location.pathname + location.search }} />
  if (usuario.trocar_senha) return <TrocaObrigatoria />
  return children
}

/** Esconde telas de outros papéis. A API recusa de qualquer forma: isto é só navegação. */
function SomentePapel({ papeis, children }: { papeis: PapelUsuario[]; children: ReactNode }) {
  const { usuario } = useAuth()
  if (!usuario || !papeis.includes(usuario.papel)) return <Navigate to="/projetos" replace />
  return children
}

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  {
    element: (
      <ComSessao>
        <Layout />
      </ComSessao>
    ),
    children: [
      { index: true, element: <Navigate to="/projetos" replace /> },
      { path: "/projetos", element: <Projetos /> },
      { path: "/projetos/:id", element: <ProjetoDetalhe /> },
      { path: "/minhas-tarefas", element: <MinhasTarefas /> },
      { path: "/usuarios", element: <SomentePapel papeis={["admin"]}><Usuarios /></SomentePapel> },
      { path: "/auditoria", element: <SomentePapel papeis={["admin"]}><Auditoria /></SomentePapel> },
      { path: "*", element: <Navigate to="/projetos" replace /> },
    ],
  },
])

export function App() {
  return <RouterProvider router={router} />
}
