import { lazy, Suspense, type ReactNode } from "react"
import { createBrowserRouter, Navigate, RouterProvider, useLocation } from "react-router"

import { TrocaObrigatoria } from "@/components/AlterarSenha"
import { Carregando } from "@/components/comum"
import { DoisFatoresObrigatorio } from "@/components/DoisFatores"
import { ErroDeTela } from "@/components/ErroDeTela"
import { Layout } from "@/components/Layout"
import { useAuth } from "@/lib/auth"
import type { PapelUsuario } from "@/lib/tipos"
import { Login } from "@/pages/Login"
import { EsqueciSenha, RedefinirSenha } from "@/pages/RecuperarSenha"

// Cada página é baixada só quando é aberta (o login não carrega o código do cronograma, do PDF...).
const Auditoria = lazy(() => import("@/pages/Auditoria").then((m) => ({ default: m.Auditoria })))
const Vigia = lazy(() => import("@/pages/Vigia").then((m) => ({ default: m.Vigia })))
const MinhasTarefas = lazy(() => import("@/pages/MinhasTarefas").then((m) => ({ default: m.MinhasTarefas })))
const ProjetoDetalhe = lazy(() => import("@/pages/ProjetoDetalhe").then((m) => ({ default: m.ProjetoDetalhe })))
const Projetos = lazy(() => import("@/pages/Projetos").then((m) => ({ default: m.Projetos })))
const Usuarios = lazy(() => import("@/pages/Usuarios").then((m) => ({ default: m.Usuarios })))

function Pagina({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Carregando />}>{children}</Suspense>
}

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
  // Abertas sem sessão: quem esqueceu a senha não consegue entrar.
  { path: "/esqueci-senha", element: <EsqueciSenha />, errorElement: <ErroDeTela /> },
  { path: "/redefinir-senha", element: <RedefinirSenha />, errorElement: <ErroDeTela /> },
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
          { path: "/projetos", element: <Pagina><Projetos /></Pagina> },
          { path: "/projetos/:id", element: <Pagina><ProjetoDetalhe /></Pagina> },
          { path: "/minhas-tarefas", element: <Pagina><MinhasTarefas /></Pagina> },
          { path: "/usuarios", element: <SomentePapel papeis={["admin"]}><Pagina><Usuarios /></Pagina></SomentePapel> },
          { path: "/auditoria", element: <SomentePapel papeis={["admin"]}><Pagina><Auditoria /></Pagina></SomentePapel> },
          { path: "/vigia", element: <SomenteVigia><Pagina><Vigia /></Pagina></SomenteVigia> },
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
