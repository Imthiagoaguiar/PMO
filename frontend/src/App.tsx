import type { ReactNode } from "react"
import { createBrowserRouter, Navigate, RouterProvider, useLocation } from "react-router"

import { TrocaObrigatoria } from "@/components/AlterarSenha"
import { Carregando } from "@/components/comum"
import { DoisFatoresObrigatorio } from "@/components/DoisFatores"
import { ErroDeTela } from "@/components/ErroDeTela"
import { Layout } from "@/components/Layout"
import { useAuth } from "@/lib/auth"
import type { PapelUsuario } from "@/lib/tipos"
import { Auditoria } from "@/pages/Auditoria"
import { Vigia } from "@/pages/Vigia"
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
  if (usuario.exige_configurar_2fa) return <DoisFatoresObrigatorio />
  return children
}

/** Esconde telas de outros papéis. A API recusa de qualquer forma: isto é só navegação. */
/** A API também barra (404): isto só evita mostrar uma página vazia a quem não pode ver. */
function SomenteVigia({ children }: { children: ReactNode }) {
  const { usuario } = useAuth()
  return usuario?.pode_ver_vigia ? children : <Navigate to="/projetos" replace />
}

function SomentePapel({ papeis, children }: { papeis: PapelUsuario[]; children: ReactNode }) {
  const { usuario } = useAuth()
  if (!usuario || !papeis.includes(usuario.papel)) return <Navigate to="/projetos" replace />
  return children
}

export const rotas = [
  { path: "/login", element: <Login />, errorElement: <ErroDeTela /> },
  {
    element: (
      <ComSessao>
        <Layout />
      </ComSessao>
    ),
    errorElement: <ErroDeTela />, // se o próprio layout quebrar: a tela de erro ocupa a página
    children: [
      {
        // Erro numa tela: aparece DENTRO do layout, com o menu para sair dali.
        errorElement: <ErroDeTela />,
        children: [
          { index: true, element: <Navigate to="/projetos" replace /> },
          { path: "/projetos", element: <Projetos /> },
          { path: "/projetos/:id", element: <ProjetoDetalhe /> },
          { path: "/minhas-tarefas", element: <MinhasTarefas /> },
          { path: "/usuarios", element: <SomentePapel papeis={["admin"]}><Usuarios /></SomentePapel> },
          { path: "/auditoria", element: <SomentePapel papeis={["admin"]}><Auditoria /></SomentePapel> },
          { path: "/vigia", element: <SomenteVigia><Vigia /></SomenteVigia> },
          { path: "*", element: <Navigate to="/projetos" replace /> },
        ],
      },
    ],
  },
]

const router = createBrowserRouter(rotas)

export function App() {
  return <RouterProvider router={router} />
}
