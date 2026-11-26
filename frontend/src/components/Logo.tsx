import { cn } from "@/lib/utils"

/** Lâmpada amarela no quadrado preto: a "ideia". O mesmo desenho do favicon (public/favicon.svg). */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-7 shrink-0", className)} aria-hidden>
      <rect width="32" height="32" rx="7" fill="#0a0a0a" />
      <path d="M16 3v1.6M7.6 6.8l1.1 1.1M24.4 6.8l-1.1 1.1M4.8 14h1.6M25.6 14h1.6" stroke="var(--marca)" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M16 7.2a6.8 6.8 0 0 0-4.1 12.2c.8.6 1.2 1.4 1.2 2.3v.5h5.8v-.5c0-.9.4-1.7 1.2-2.3A6.8 6.8 0 0 0 16 7.2z" fill="var(--marca)" />
      <path d="M13.4 13.6a2.8 2.8 0 0 1 2.6-2.6" fill="none" stroke="#fff" strokeWidth="1.2" strokeLinecap="round" opacity=".8" />
      <rect x="13" y="23.4" width="6" height="1.5" rx=".75" fill="#fff" />
      <rect x="14" y="25.8" width="4" height="1.5" rx=".75" fill="#fff" />
    </svg>
  )
}

/** Fundo preto com a marca, para as telas fora do sistema (entrar, recuperar senha, 2FA, troca obrigatória). */
export function TelaDaMarca({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-h-svh flex-col items-center justify-center gap-6 bg-[#0a0a0a] px-4 py-8", className)}>
      <div className="flex items-center gap-3 text-white">
        <Logo className="size-11" />
        <div>
          <p className="text-3xl font-extrabold leading-none tracking-tight">OMPY</p>
          <p className="mt-1 text-sm text-white/70">Gestão de projetos, <span className="text-marca">da ideia à entrega</span></p>
        </div>
      </div>
      {children}
    </div>
  )
}
