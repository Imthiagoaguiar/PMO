import { AlertTriangle, RefreshCw } from "lucide-react"
import { isRouteErrorResponse, Link, useRouteError } from "react-router"

import { Button } from "@/components/ui/button"

/** Depois de um deploy, a aba aberta pede arquivos da versão antiga, que não existem mais. */
function ehVersaoDesatualizada(erro: unknown): boolean {
  const mensagem = erro instanceof Error ? erro.message : String(erro)
  return /dynamically imported module|Importing a module script failed|ChunkLoadError|Loading chunk/i.test(mensagem)
}

/**
 * Erro inesperado ao desenhar uma tela (bug no front, dado fora do formato...). Sem isto, o React Router
 * mostra a página padrão dele, em inglês e com o stack trace. Os detalhes vão só para o console.
 */
export function ErroDeTela() {
  const erro = useRouteError()
  console.error("Erro ao exibir a tela:", erro)

  const desatualizada = ehVersaoDesatualizada(erro)
  const naoEncontrada = isRouteErrorResponse(erro) && erro.status === 404
  const titulo = desatualizada
    ? "O sistema foi atualizado"
    : naoEncontrada
      ? "Página não encontrada"
      : "Algo deu errado nesta tela"
  const texto = desatualizada
    ? "Recarregue a página para usar a versão nova."
    : naoEncontrada
      ? "O endereço não existe ou foi removido."
      : "Seus dados estão salvos. Tente recarregar; se o erro continuar, avise o administrador do sistema."

  return (
    <div role="alert" className="mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center">
      <AlertTriangle className="size-10 text-amber-500" aria-hidden />
      <div>
        <h1 className="text-xl font-semibold">{titulo}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{texto}</p>
      </div>
      <div className="flex gap-2">
        <Button onClick={() => window.location.reload()}>
          <RefreshCw /> Recarregar
        </Button>
        <Button variant="outline" asChild>
          <Link to="/projetos" reloadDocument>
            Ir para os projetos
          </Link>
        </Button>
      </div>
    </div>
  )
}
