import { ChevronDown, FolderKanban, KeyRound, ListChecks, LogOut, Radar, ScrollText, ShieldCheck, Users } from "lucide-react"
import { useState } from "react"
import { NavLink, Outlet, useNavigate } from "react-router"

import { AlterarSenha } from "@/components/AlterarSenha"
import { NOMES_PAPEL_USUARIO } from "@/components/comum"
import { DoisFatoresDialog } from "@/components/DoisFatores"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth, useUsuario } from "@/lib/auth"
import { cn } from "@/lib/utils"

function ItemMenu({ para, icone: Icone, children }: { para: string; icone: typeof Users; children: string }) {
  return (
    <NavLink
      to={para}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
          isActive && "bg-accent text-foreground",
        )
      }
    >
      <Icone className="size-4" />
      <span className="hidden sm:inline">{children}</span>
    </NavLink>
  )
}

export function Layout() {
  const usuario = useUsuario()
  const { sair } = useAuth()
  const navigate = useNavigate()
  const [alterandoSenha, setAlterandoSenha] = useState(false)
  const [configurando2fa, setConfigurando2fa] = useState(false)
  const admin = usuario.papel === "admin"

  async function encerrar() {
    await sair()
    navigate("/login")
  }

  return (
    <div className="min-h-svh">
      <header className="sticky top-0 z-30 border-b bg-card/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-2 px-4">
          <NavLink to="/projetos" className="mr-4 flex items-center gap-2 font-semibold">
            <span className="flex size-7 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
              PMO
            </span>
            <span className="hidden md:inline">Waterfall</span>
          </NavLink>
          <nav className="flex flex-1 items-center gap-1">
            <ItemMenu para="/projetos" icone={FolderKanban}>Projetos</ItemMenu>
            <ItemMenu para="/minhas-tarefas" icone={ListChecks}>Minhas tarefas</ItemMenu>
            {admin && <ItemMenu para="/usuarios" icone={Users}>Usuários</ItemMenu>}
            {admin && <ItemMenu para="/auditoria" icone={ScrollText}>Auditoria</ItemMenu>}
            {usuario.pode_ver_vigia && <ItemMenu para="/vigia" icone={Radar}>Vigia</ItemMenu>}
          </nav>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" className="gap-2">
                <span className="flex size-7 items-center justify-center rounded-full bg-secondary text-xs font-semibold uppercase">
                  {usuario.nome.slice(0, 2)}
                </span>
                <span className="hidden max-w-40 truncate md:inline">{usuario.nome}</span>
                <ChevronDown className="size-4 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>
                <div className="truncate">{usuario.nome}</div>
                <div className="truncate text-xs font-normal text-muted-foreground">{usuario.email}</div>
                <div className="text-xs font-normal text-muted-foreground">{NOMES_PAPEL_USUARIO[usuario.papel]}</div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              {usuario.provedor_auth === "local" && (
                <DropdownMenuItem onSelect={() => setAlterandoSenha(true)}>
                  <KeyRound /> Alterar senha
                </DropdownMenuItem>
              )}
              {usuario.provedor_auth === "local" && (
                <DropdownMenuItem onSelect={() => setConfigurando2fa(true)}>
                  <ShieldCheck /> Verificação em duas etapas
                  {usuario.dois_fatores_ativo && <span className="ml-auto text-xs text-emerald-600">ativa</span>}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={encerrar}>
                <LogOut /> Sair
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-8">
        <Outlet />
      </main>
      <AlterarSenha aberto={alterandoSenha} aoFechar={() => setAlterandoSenha(false)} />
      <DoisFatoresDialog aberto={configurando2fa} aoFechar={() => setConfigurando2fa(false)} />
    </div>
  )
}
