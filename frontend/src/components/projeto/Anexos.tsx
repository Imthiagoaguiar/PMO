import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query"
import { Download, FileText, Paperclip, Trash2, Upload } from "lucide-react"
import { useRef, useState } from "react"
import { toast } from "sonner"

import { Carregando, ErroCarregar, Paginacao, Vazio } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api, baixarArquivo } from "@/lib/api"
import { useAtualizarProjeto } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import type { Anexo, Pagina, Tarefa } from "@/lib/tipos"

const MAX_MB = 25
const POR_PAGINA = 30

export function tamanhoLegivel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} KB`
  return `${(bytes / 1024 / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`
}

function useAcoesDeAnexo(projetoId: number) {
  const atualizar = useAtualizarProjeto(projetoId)
  const [baixando, setBaixando] = useState<number | null>(null)
  const excluir = useMutation({
    mutationFn: (a: Anexo) => api(`/projetos/${projetoId}/anexos/${a.id}`, { method: "DELETE" }),
    onSuccess: () => {
      atualizar()
      toast.success("Arquivo excluído.")
    },
    onError: fmt.notificarErro,
  })
  async function baixar(a: Anexo) {
    setBaixando(a.id)
    try {
      await baixarArquivo(`/projetos/${projetoId}/anexos/${a.id}/download`, a.nome_original)
    } catch (erro) {
      fmt.notificarErro(erro)
    } finally {
      setBaixando(null)
    }
  }
  function confirmarExclusao(a: Anexo) {
    if (window.confirm(`Excluir “${a.nome_original}”? A exclusão fica registrada no histórico.`)) excluir.mutate(a)
  }
  return { baixar, baixando, confirmarExclusao, excluindo: excluir.isPending ? excluir.variables?.id : null }
}

/** Seção do diálogo da tarefa: enviar arquivos (até 25 MB) e ver/baixar os desta tarefa. */
export function AnexosDaTarefa({ projetoId, tarefa, podeEditar }: { projetoId: number; tarefa: Tarefa; podeEditar: boolean }) {
  const atualizar = useAtualizarProjeto(projetoId)
  const entrada = useRef<HTMLInputElement>(null)
  const lista = useQuery({
    queryKey: ["projeto", projetoId, "anexos", "tarefa", tarefa.id],
    queryFn: () => api<Pagina<Anexo>>(`/projetos/${projetoId}/anexos?tarefa_id=${tarefa.id}&limite=200`),
  })
  const acoes = useAcoesDeAnexo(projetoId)
  const enviar = useMutation({
    mutationFn: (arquivos: File[]) =>
      Promise.all(arquivos.map((arquivo) => {
        const form = new FormData()
        form.append("arquivo", arquivo)
        return api<Anexo>(`/projetos/${projetoId}/tarefas/${tarefa.id}/anexos`, { method: "POST", form })
      })),
    onSuccess: (enviados) => {
      atualizar()
      toast.success(enviados.length === 1 ? `“${enviados[0].nome_original}” anexado.` : `${enviados.length} arquivos anexados.`)
    },
    onError: (erro) => {
      atualizar() // os que deram certo antes do erro aparecem
      fmt.notificarErro(erro)
    },
  })

  function escolher(arquivos: FileList | null) {
    const todos = Array.from(arquivos ?? [])
    const grandes = todos.filter((a) => a.size > MAX_MB * 1024 * 1024)
    if (grandes.length) toast.error(`Maior que ${MAX_MB} MB: ${grandes.map((a) => a.name).join(", ")}`)
    const validos = todos.filter((a) => a.size <= MAX_MB * 1024 * 1024)
    if (validos.length) enviar.mutate(validos)
    if (entrada.current) entrada.current.value = ""
  }

  return (
    <section aria-label="Arquivos da tarefa" className="grid gap-2 rounded-md border p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 font-medium"><Paperclip className="size-4" /> Arquivos</h3>
        {podeEditar && (
          <>
            <input ref={entrada} type="file" multiple className="sr-only" aria-label="Escolher arquivos para anexar"
              onChange={(e) => escolher(e.target.files)} />
            <Button type="button" size="sm" variant="outline" carregando={enviar.isPending} onClick={() => entrada.current?.click()}>
              <Upload /> {enviar.isPending ? "Enviando..." : "Anexar"}
            </Button>
          </>
        )}
      </div>
      {lista.isPending ? <p className="text-muted-foreground">Carregando arquivos...</p> : lista.data?.itens.length ? (
        <ul className="grid gap-1">
          {lista.data.itens.map((a) => (
            <li key={a.id} className="flex items-center gap-2">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate" title={a.nome_original}>{a.nome_original}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{tamanhoLegivel(a.tamanho)} · {fmt.dataHora(a.enviado_em)}</span>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`Baixar ${a.nome_original}`}
                carregando={acoes.baixando === a.id} onClick={() => acoes.baixar(a)}>
                <Download />
              </Button>
              {podeEditar && (
                <Button type="button" variant="ghost" size="icon-sm" aria-label={`Excluir ${a.nome_original}`}
                  carregando={acoes.excluindo === a.id} onClick={() => acoes.confirmarExclusao(a)}>
                  <Trash2 />
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">Nenhum arquivo. Até {MAX_MB} MB cada (programas e scripts não são aceitos).</p>
      )}
    </section>
  )
}

/** Aba "Arquivos": histórico de todos os arquivos do projeto, inclusive de tarefas já excluídas. */
export function HistoricoDeArquivos({ projetoId, podeEditar }: { projetoId: number; podeEditar: boolean }) {
  const [offset, setOffset] = useState(0)
  const lista = useQuery({
    queryKey: ["projeto", projetoId, "anexos", "todos", offset],
    queryFn: () => api<Pagina<Anexo>>(`/projetos/${projetoId}/anexos?limite=${POR_PAGINA}&offset=${offset}`),
    placeholderData: keepPreviousData,
  })
  const acoes = useAcoesDeAnexo(projetoId)

  if (lista.isPending) return <Carregando texto="Carregando arquivos..." />
  if (lista.isError) return <ErroCarregar erro={lista.error} />
  if (lista.data.total === 0) {
    return (
      <Vazio titulo="Nenhum arquivo anexado">
        Abra uma tarefa (lápis) e use “Anexar” para guardar documentos, atas, contratos e evidências.
      </Vazio>
    )
  }
  const usado = lista.data.itens.reduce((s, a) => s + a.tamanho, 0)
  return (
    <div className="grid gap-3">
      <p className="text-sm text-muted-foreground">
        {fmt.pluralizar(lista.data.total, "arquivo", "arquivos")} no projeto, do mais recente ao mais antigo.
        {lista.data.total <= POR_PAGINA && ` Total: ${tamanhoLegivel(usado)}.`}
      </p>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-4">Arquivo</TableHead>
              <TableHead>Tarefa</TableHead>
              <TableHead>Enviado por</TableHead>
              <TableHead>Quando</TableHead>
              <TableHead className="text-right">Tamanho</TableHead>
              <TableHead className="w-24 pr-4"><span className="sr-only">Ações</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {lista.data.itens.map((a) => (
              <TableRow key={a.id}>
                <TableCell className="max-w-xs pl-4 whitespace-normal">{a.nome_original}</TableCell>
                <TableCell className="max-w-xs whitespace-normal text-muted-foreground">
                  {a.tarefa_descricao}
                  {a.tarefa_id === null && <span className="ml-1 text-xs">(tarefa excluída)</span>}
                </TableCell>
                <TableCell className="text-muted-foreground">{a.enviado_por?.nome ?? "—"}</TableCell>
                <TableCell className="tabular-nums text-muted-foreground">{fmt.dataHora(a.enviado_em)}</TableCell>
                <TableCell className="text-right tabular-nums">{tamanhoLegivel(a.tamanho)}</TableCell>
                <TableCell className="pr-4">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="icon-sm" aria-label={`Baixar ${a.nome_original}`}
                      carregando={acoes.baixando === a.id} onClick={() => acoes.baixar(a)}>
                      <Download />
                    </Button>
                    {podeEditar && (
                      <Button variant="ghost" size="icon-sm" aria-label={`Excluir ${a.nome_original}`}
                        carregando={acoes.excluindo === a.id} onClick={() => acoes.confirmarExclusao(a)}>
                        <Trash2 />
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Paginacao total={lista.data.total} limite={POR_PAGINA} offset={offset} aoMudar={setOffset} />
    </div>
  )
}
