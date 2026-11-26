import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, Download, Eye, FileText, Lock, MoreHorizontal, Pencil, Presentation, Trash2, Loader2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { toast } from "sonner"

import { BarraProgresso, Campo, Carregando, ErroCarregar, StatusBadge } from "@/components/comum"
import { HistoricoDeArquivos } from "@/components/projeto/Anexos"
import { Comentarios } from "@/components/projeto/Comentarios"
import { Cronograma } from "@/components/projeto/Cronograma"
import { CurvaS } from "@/components/projeto/CurvaS"
import { Feedback } from "@/components/projeto/Feedback"
import { Gargalos } from "@/components/projeto/Gargalos"
import { Historico } from "@/components/projeto/Historico"
import { LinhaDoTempo } from "@/components/projeto/LinhaDoTempo"
import { Membros } from "@/components/projeto/Membros"
import { MontadorSlides } from "@/components/projeto/MontadorSlides"
import { Orcamento } from "@/components/projeto/Orcamento"
import { Pilares } from "@/components/projeto/Pilares"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, baixarArquivo, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto, useIndicadores, useMembros, useProjeto } from "@/lib/consultas"
import * as fmt from "@/lib/formato"
import { STATUS_PROJETO, type Projeto, type StatusProjeto } from "@/lib/tipos"

const ENCERRADOS: StatusProjeto[] = ["Concluído", "Cancelado"]

function EditarProjeto({ projeto, aoFechar }: { projeto: Projeto; aoFechar: () => void }) {
  const atualizar = useAtualizarProjeto(projeto.id)
  const [nome, setNome] = useState(projeto.nome_projeto)
  const [orcamento, setOrcamento] = useState(projeto.orcamento_aprovado ?? "")
  const [dataInicio, setDataInicio] = useState(projeto.data_inicio ?? "")

  const salvar = useMutation({
    mutationFn: () => {
      const dados: Record<string, unknown> = {}
      if (nome.trim() !== projeto.nome_projeto) dados.nome_projeto = nome.trim()
      const novoOrcamento = orcamento === "" ? null : String(orcamento)
      if (novoOrcamento === null ? projeto.orcamento_aprovado !== null : Number(novoOrcamento) !== Number(projeto.orcamento_aprovado))
        dados.orcamento_aprovado = novoOrcamento
      if ((dataInicio || null) !== projeto.data_inicio) dados.data_inicio = dataInicio || null
      return api(`/projetos/${projeto.id}`, { method: "PATCH", json: { ...dados, versao: projeto.versao } })
    },
    onSuccess: () => {
      atualizar()
      toast.success("Projeto atualizado.")
      aoFechar()
    },
    onError: (erro) => {
      fmt.notificarErro(erro)
      if (ehConflitoDeVersao(erro)) {
        atualizar()
        aoFechar()
      }
    },
  })

  function enviar(e: FormEvent) {
    e.preventDefault()
    salvar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Editar projeto</DialogTitle>
          </DialogHeader>
          <Campo label="Nome" htmlFor="p-nome">
            <Input id="p-nome" required minLength={3} maxLength={200} value={nome} onChange={(e) => setNome(e.target.value)} />
          </Campo>
          <div className="grid grid-cols-2 gap-4">
            <Campo label="Orçamento (R$)" htmlFor="p-orcamento">
              <Input id="p-orcamento" type="number" min={0} step="0.01" value={orcamento} onChange={(e) => setOrcamento(e.target.value)} />
            </Campo>
            <Campo label="Início" htmlFor="p-inicio">
              <Input id="p-inicio" type="date" value={dataInicio} onChange={(e) => setDataInicio(e.target.value)} />
            </Campo>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>
              Cancelar
            </Button>
            <Button type="submit" disabled={salvar.isPending || nome.trim().length < 3}>
              Salvar
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function Indicador({ titulo, valor, detalhe, destaque }: { titulo: string; valor: string; detalhe?: string; destaque?: boolean }) {
  return (
    <Card className="gap-1 py-4">
      <CardHeader className="px-4">
        <CardDescription>{titulo}</CardDescription>
        <CardTitle className={destaque ? "text-2xl text-destructive" : "text-2xl"}>{valor}</CardTitle>
      </CardHeader>
      {detalhe && <CardContent className="px-4 text-xs text-muted-foreground">{detalhe}</CardContent>}
    </Card>
  )
}

export function ProjetoDetalhe() {
  const id = Number(useParams().id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const projeto = useProjeto(id)
  const indicadores = useIndicadores(id)
  const membros = useMembros(id)
  const atualizar = useAtualizarProjeto(id)
  const [editando, setEditando] = useState(false)
  const [excluindo, setExcluindo] = useState(false)
  const [exportando, setExportando] = useState(false)
  const [gerandoPdf, setGerandoPdf] = useState(false)
  const [montandoSlides, setMontandoSlides] = useState(false)

  const mudarStatus = useMutation({
    mutationFn: (status: StatusProjeto) =>
      api(`/projetos/${id}`, { method: "PATCH", json: { status, versao: projeto.data?.versao } }),
    onSuccess: (_, status) => {
      atualizar()
      toast.success(ENCERRADOS.includes(status) ? `Projeto ${status.toLowerCase()}: agora somente leitura.` : `Status: ${status}.`)
    },
    onError: (erro) => {
      fmt.notificarErro(erro)
      if (ehConflitoDeVersao(erro)) atualizar()
    },
  })

  const excluir = useMutation({
    mutationFn: () => api(`/projetos/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      // Sai da tela antes de limpar o cache: recarregar o projeto excluído daria 404.
      navigate("/projetos")
      queryClient.removeQueries({ queryKey: ["projeto", id] })
      queryClient.invalidateQueries({ queryKey: ["projetos"] })
      toast.success("Projeto excluído.")
    },
    onError: fmt.notificarErro,
  })

  async function gerarPdf() {
    setGerandoPdf(true)
    try {
      await baixarArquivo(`/projetos/${id}/relatorio`, "relatorio.pdf")
    } catch (erro) {
      fmt.notificarErro(erro)
    } finally {
      setGerandoPdf(false)
    }
  }

  async function exportar() {
    setExportando(true)
    try {
      await baixarArquivo(`/projetos/${id}/exportar`, "projeto.xlsx")
    } catch (erro) {
      fmt.notificarErro(erro)
    } finally {
      setExportando(false)
    }
  }

  if (projeto.isPending) return <Carregando />
  if (projeto.isError) {
    return (
      <div className="grid gap-4">
        <ErroCarregar erro={projeto.error} />
        <Link to="/projetos" className="text-sm text-primary hover:underline">
          Voltar para os projetos
        </Link>
      </div>
    )
  }

  const p = projeto.data
  const permissoes = p.permissoes ?? { editar: false, gerenciar: false, comentar: true }
  const encerrado = ENCERRADOS.includes(p.status)
  const podeEditarCronograma = permissoes.editar && !encerrado
  const ind = indicadores.data

  return (
    <>
      <Link to="/projetos" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Projetos
      </Link>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{p.nome_projeto}</h1>
            {!permissoes.editar && <StatusBadge status={p.status} />}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Início {fmt.data(p.data_inicio)} · Orçamento {fmt.moeda(p.orcamento_aprovado)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {permissoes.editar && (
            <Select value={p.status} onValueChange={(v) => mudarStatus.mutate(v as StatusProjeto)} disabled={mudarStatus.isPending}>
              <SelectTrigger className="w-44" aria-label="Status do projeto">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_PROJETO.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button carregando={gerandoPdf} variant="outline" onClick={gerarPdf} disabled={gerandoPdf}>
            <FileText /> {gerandoPdf ? "Gerando PDF..." : "Relatório PDF"}
          </Button>
          <Button variant="outline" onClick={() => setMontandoSlides(true)}>
            <Presentation /> PowerPoint
          </Button>
          <Button carregando={exportando} variant="outline" onClick={exportar} disabled={exportando}>
            <Download /> {exportando ? "Exportando..." : "Exportar Excel"}
          </Button>
          {(permissoes.editar || permissoes.gerenciar) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon" aria-label="Mais ações">
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {permissoes.editar && (
                  <DropdownMenuItem disabled={encerrado} onSelect={() => setEditando(true)}>
                    <Pencil /> Editar dados
                  </DropdownMenuItem>
                )}
                {permissoes.gerenciar && (
                  <DropdownMenuItem variant="destructive" onSelect={() => setExcluindo(true)}>
                    <Trash2 /> Excluir projeto
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      {encerrado && (
        <div className="mb-6 flex items-center gap-2 rounded-lg border bg-muted/50 px-4 py-3 text-sm">
          <Lock className="size-4 text-muted-foreground" />
          Projeto {p.status.toLowerCase()}: somente leitura.
          {permissoes.editar && " Para editar, altere o status para reabrir."}
        </div>
      )}
      {!permissoes.editar && !encerrado && (
        <div className="mb-6 flex items-center gap-2 rounded-lg border bg-muted/50 px-4 py-3 text-sm">
          <Eye className="size-4 text-muted-foreground" />
          Você tem acesso de visualização: pode ver, exportar e comentar.
        </div>
      )}

      {ind && (
        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Card className="gap-2 py-4">
            <CardHeader className="px-4">
              <CardDescription>Andamento geral</CardDescription>
              <CardTitle className="text-2xl">{Math.round(ind.percentual_conclusao)}%</CardTitle>
            </CardHeader>
            <CardContent className="px-4">
              <BarraProgresso valor={ind.percentual_conclusao} />
            </CardContent>
          </Card>
          <Indicador
            titulo="Tarefas concluídas"
            valor={`${ind.tarefas_concluidas} de ${ind.total_tarefas}`}
            detalhe={
              !p.seguir_ordem_fases ? "Fases em paralelo"
              : ind.fase_atual ? `Fase atual: ${ind.fase_atual}` : ind.total_tarefas ? "Todas as fases concluídas" : "Sem tarefas ainda"
            }
          />
          <Indicador titulo="Tarefas atrasadas" valor={String(ind.tarefas_atrasadas)} destaque={ind.tarefas_atrasadas > 0} detalhe="Prazo vencido e não concluídas" />
          <Indicador
            titulo="Prazo previsto"
            valor={fmt.data(ind.data_fim_prevista)}
            detalhe={ind.data_inicio_prevista ? `Desde ${fmt.data(ind.data_inicio_prevista)}` : "Defina datas nas tarefas"}
          />
        </div>
      )}

      <Tabs defaultValue="cronograma">
        <TabsList className="mb-4 h-auto flex-wrap">
          <TabsTrigger value="cronograma">Cronograma</TabsTrigger>
          <TabsTrigger value="pilares">Pilares</TabsTrigger>
          <TabsTrigger value="orcamento">Orçamento</TabsTrigger>
          <TabsTrigger value="linha">Linha do tempo</TabsTrigger>
          <TabsTrigger value="curva-s">Curva S</TabsTrigger>
          <TabsTrigger value="gargalos">Gargalos</TabsTrigger>
          {permissoes.gerenciar && <TabsTrigger value="feedback">Feedback</TabsTrigger>}
          <TabsTrigger value="comentarios">Comentários</TabsTrigger>
          <TabsTrigger value="arquivos">Arquivos</TabsTrigger>
          <TabsTrigger value="membros">Membros</TabsTrigger>
          <TabsTrigger value="historico">Histórico</TabsTrigger>
        </TabsList>
        <TabsContent value="cronograma">
          <Cronograma projeto={p} indicadores={ind} membros={membros.data ?? []} podeEditar={podeEditarCronograma} />
        </TabsContent>
        <TabsContent value="pilares">
          <Pilares projetoId={p.id} podeEditar={podeEditarCronograma} />
        </TabsContent>
        <TabsContent value="orcamento">
          <Orcamento projeto={p} podeEditar={podeEditarCronograma} />
        </TabsContent>
        <TabsContent value="linha">
          <LinhaDoTempo projeto={p} podeEditar={podeEditarCronograma} />
        </TabsContent>
        <TabsContent value="curva-s">
          <CurvaS projetoId={p.id} />
        </TabsContent>
        {permissoes.gerenciar && (
          <TabsContent value="feedback">
            <Feedback projetoId={p.id} />
          </TabsContent>
        )}
        <TabsContent value="gargalos">
          <Gargalos projetoId={p.id} />
        </TabsContent>
        <TabsContent value="arquivos">
          <HistoricoDeArquivos projetoId={p.id} podeEditar={podeEditarCronograma} />
        </TabsContent>
        <TabsContent value="comentarios" className="max-w-3xl">
          <Comentarios projeto={p} />
        </TabsContent>
        <TabsContent value="membros" className="max-w-3xl">
          <Membros projeto={p} />
        </TabsContent>
        <TabsContent value="historico" className="max-w-3xl">
          <Historico projetoId={p.id} />
        </TabsContent>
      </Tabs>

      {editando && <EditarProjeto projeto={p} aoFechar={() => setEditando(false)} />}
      {montandoSlides && <MontadorSlides projeto={p} aoFechar={() => setMontandoSlides(false)} />}

      <AlertDialog open={excluindo} onOpenChange={setExcluindo}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir “{p.nome_projeto}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Fases, tarefas, membros e comentários serão apagados. Não dá para desfazer. O registro da exclusão continua na auditoria.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={(e) => {
                e.preventDefault()
                excluir.mutate()
              }}
            >
              {excluir.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {excluir.isPending ? "Excluindo..." : "Excluir projeto"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
