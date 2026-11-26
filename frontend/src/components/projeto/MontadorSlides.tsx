import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowDown, ArrowUp, FileUp, Plus, Presentation, StickyNote, Trash2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api, baixarArquivo } from "@/lib/api"
import { useUsuario } from "@/lib/auth"
import * as fmt from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { ModeloApresentacao, Projeto, TipoSlide } from "@/lib/tipos"

interface ItemSlide {
  id: string
  tipo: TipoSlide
  incluir: boolean
  titulo: string
  texto: string
  notas: string
}

const NOMES: Record<TipoSlide, string> = {
  capa: "Capa", resumo: "Resumo e indicadores", curva_s: "Curva S", linha_do_tempo: "Linha do tempo com marcos",
  marcos: "Marcos", pilares: "Pilares", orcamento: "Orçamento", gargalos: "Gargalos", atencao: "Pontos de atenção", texto: "Slide de texto",
}
const TITULO_PADRAO: Record<TipoSlide, string> = {
  capa: "", resumo: "Resumo", curva_s: "Curva S: planejado x realizado", linha_do_tempo: "Linha do tempo", marcos: "Marcos",
  pilares: "Pilares", orcamento: "Orçamento", gargalos: "Gargalos: onde o prazo escapou", atencao: "Pontos de atenção", texto: "Comentários",
}
const CONTEUDO: Record<TipoSlide, string> = {
  capa: "Nome do projeto, status, período, responsável e data de geração.",
  resumo: "4 indicadores (andamento, concluídas, atrasadas, prazo) e o gráfico de andamento por fase.",
  curva_s: "Curva S física (% do cronograma) e financeira (R$), planejado x realizado, e o desvio de hoje.",
  linha_do_tempo: "Uma barra por fase com o % feito, os marcos (pins, bandeiras, formas), períodos e a linha de hoje.",
  marcos: "Tabela com data, título e descrição de cada marco.",
  pilares: "Tabela com farol, andamento, tarefas, orçado x realizado e situação de cada pilar.",
  orcamento: "Orçado, realizado, saldo e previsão; gráfico orçado x realizado por item e alertas.",
  gargalos: "Dias de atraso por responsável e as tarefas mais atrasadas.",
  atencao: "Tarefas atrasadas e os próximos marcos.",
  texto: "O texto que você escrever, um tópico por linha.",
}
const PADRAO: TipoSlide[] = ["capa", "resumo", "curva_s", "linha_do_tempo", "marcos", "pilares", "orcamento", "gargalos", "atencao"]
const PADRAO_PMO = "padrao"

function novoId() {
  return Math.random().toString(36).slice(2, 10)
}

function inicial(projetoId: number): ItemSlide[] {
  try {
    const salvo = localStorage.getItem(`pmo:slides:${projetoId}`)
    if (salvo) {
      const lista = JSON.parse(salvo) as ItemSlide[]
      if (Array.isArray(lista) && lista.every((i) => i && typeof i.tipo === "string" && i.tipo in NOMES)) return lista
    }
  } catch {
    // armazenamento indisponível (aba anônima, bloqueado): começa do padrão
  }
  return PADRAO.map((tipo) => ({ id: novoId(), tipo, incluir: true, titulo: "", texto: "", notas: "" }))
}

function Previa({ item, projeto }: { item: ItemSlide; projeto: Projeto }) {
  const titulo = item.titulo || (item.tipo === "capa" ? projeto.nome_projeto : TITULO_PADRAO[item.tipo])
  const linhas = item.texto.split("\n").map((l) => l.replace(/^[-•*\s]+/, "").trim()).filter(Boolean)
  return (
    <div className="grid gap-2">
      <div className={cn("relative flex aspect-video flex-col overflow-hidden rounded-md border bg-white p-4 text-slate-800 shadow-sm",
        !item.incluir && "opacity-50")}>
        {item.tipo === "capa" ? (
          <div className="flex h-full flex-col justify-center border-l-8 border-blue-700 pl-4">
            <p className="text-xl font-bold leading-tight">{titulo}</p>
            <p className="mt-2 text-xs text-slate-500">Status · período · responsável</p>
          </div>
        ) : (
          <>
            <div className="mb-2 h-1 w-full bg-blue-700" />
            <p className="text-base font-bold leading-tight">{titulo}</p>
            {item.tipo === "texto" ? (
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
                {(linhas.length ? linhas : ["(escreva os tópicos ao lado)"]).slice(0, 10).map((l, i) => <li key={i}>{l}</li>)}
              </ul>
            ) : (
              <div className="mt-3 flex flex-1 items-center justify-center rounded border border-dashed border-slate-300 bg-slate-50 p-3 text-center text-xs text-slate-500">
                {CONTEUDO[item.tipo]}
              </div>
            )}
          </>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Prévia esquemática: no arquivo, os dados e gráficos reais do projeto (editáveis no PowerPoint).
        {!item.incluir && " Este slide está desligado."}
      </p>
      {item.notas && (
        <div className="rounded-md border bg-muted/40 p-2 text-xs"><span className="font-medium">Notas do apresentador:</span> {item.notas}</div>
      )}
    </div>
  )
}

function Modelos({ escolhido, aoEscolher }: { escolhido: string; aoEscolher: (v: string) => void }) {
  const usuario = useUsuario()
  const queryClient = useQueryClient()
  const lista = useQuery({ queryKey: ["modelos-apresentacao"], queryFn: () => api<ModeloApresentacao[]>("/modelos-apresentacao") })
  const entrada = useRef<HTMLInputElement>(null)
  const recarregar = () => queryClient.invalidateQueries({ queryKey: ["modelos-apresentacao"] })
  const enviar = useMutation({
    mutationFn: (arquivo: File) => {
      const form = new FormData()
      form.append("arquivo", arquivo)
      form.append("nome", arquivo.name.replace(/\.(pptx|potx)$/i, "").slice(0, 100) || "Modelo da empresa")
      return api<ModeloApresentacao>("/modelos-apresentacao", { method: "POST", form })
    },
    onSuccess: (m) => {
      recarregar()
      aoEscolher(String(m.id))
      toast.success(`Modelo “${m.nome}” disponível para todos.`)
    },
    onError: fmt.notificarErro,
  })
  const excluir = useMutation({
    mutationFn: (id: string) => api(`/modelos-apresentacao/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      recarregar()
      aoEscolher(PADRAO_PMO)
      toast.success("Modelo excluído.")
    },
    onError: fmt.notificarErro,
  })
  const admin = usuario.papel === "admin"
  const nome = lista.data?.find((m) => String(m.id) === escolhido)?.nome
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-muted-foreground">Visual:</span>
      <Select value={escolhido} onValueChange={aoEscolher}>
        <SelectTrigger className="w-64" aria-label="Modelo da apresentação"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={PADRAO_PMO}>Padrão do PMO (16:9)</SelectItem>
          {(lista.data ?? []).map((m) => <SelectItem key={m.id} value={String(m.id)}>{m.nome}</SelectItem>)}
        </SelectContent>
      </Select>
      {admin && (
        <>
          <input ref={entrada} type="file" accept=".pptx,.potx" className="sr-only" aria-label="Arquivo do modelo da empresa"
            onChange={(e) => {
              const arquivo = e.target.files?.[0]
              if (arquivo) enviar.mutate(arquivo)
              e.target.value = ""
            }} />
          <Button type="button" size="sm" variant="outline" carregando={enviar.isPending} onClick={() => entrada.current?.click()}>
            <FileUp /> {enviar.isPending ? "Enviando..." : "Enviar modelo da empresa"}
          </Button>
          {escolhido !== PADRAO_PMO && (
            <Button type="button" size="sm" variant="ghost" className="text-destructive" carregando={excluir.isPending}
              onClick={() => window.confirm(`Excluir o modelo “${nome}” para todos?`) && excluir.mutate(escolhido)}>
              <Trash2 /> Excluir modelo
            </Button>
          )}
        </>
      )}
      {!admin && (lista.data ?? []).length === 0 && (
        <span className="text-xs text-muted-foreground">Peça a um administrador para enviar o modelo .pptx da empresa.</span>
      )}
    </div>
  )
}

export function MontadorSlides({ projeto, aoFechar }: { projeto: Projeto; aoFechar: () => void }) {
  const [itens, setItens] = useState<ItemSlide[]>(() => inicial(projeto.id))
  const [selecionado, setSelecionado] = useState(itens[0]?.id)
  const [modelo, setModelo] = useState(PADRAO_PMO)
  const [gerando, setGerando] = useState(false)
  const atual = itens.find((i) => i.id === selecionado) ?? itens[0]
  const incluidos = itens.filter((i) => i.incluir)

  useEffect(() => {
    try {
      localStorage.setItem(`pmo:slides:${projeto.id}`, JSON.stringify(itens))
    } catch {
      // sem armazenamento: só não lembra a montagem
    }
  }, [itens, projeto.id])

  const mudar = (id: string, campos: Partial<ItemSlide>) => setItens((l) => l.map((i) => (i.id === id ? { ...i, ...campos } : i)))
  function mover(indice: number, delta: number) {
    setItens((l) => {
      const alvo = indice + delta
      if (alvo < 0 || alvo >= l.length) return l
      const copia = [...l]
      ;[copia[indice], copia[alvo]] = [copia[alvo], copia[indice]]
      return copia
    })
  }
  function adicionarTexto() {
    const item: ItemSlide = { id: novoId(), tipo: "texto", incluir: true, titulo: "", texto: "", notas: "" }
    setItens((l) => [...l, item])
    setSelecionado(item.id)
  }
  function restaurar() {
    const padrao = PADRAO.map((tipo) => ({ id: novoId(), tipo, incluir: true, titulo: "", texto: "", notas: "" }))
    setItens(padrao)
    setSelecionado(padrao[0].id)
  }

  async function gerar() {
    setGerando(true)
    try {
      await baixarArquivo(`/projetos/${projeto.id}/apresentacao`, "apresentacao.pptx", {
        method: "POST",
        json: {
          modelo_id: modelo === PADRAO_PMO ? null : Number(modelo),
          slides: incluidos.map((i) => ({ tipo: i.tipo, titulo: i.titulo || null, texto: i.texto || null, notas: i.notas || null })),
        },
      })
      toast.success("Apresentação baixada: abra no PowerPoint (os gráficos são editáveis).")
    } catch (erro) {
      fmt.notificarErro(erro)
    } finally {
      setGerando(false)
    }
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="max-h-[94svh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Presentation className="size-5" /> Montar apresentação</DialogTitle>
          <DialogDescription>
            Escolha os slides, a ordem e os títulos, escreva notas e slides próprios. O arquivo sai com os dados reais do projeto.
          </DialogDescription>
        </DialogHeader>

        <Modelos escolhido={modelo} aoEscolher={setModelo} />

        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="grid content-start gap-2">
            <ol className="grid gap-1.5" aria-label="Slides da apresentação">
              {itens.map((item, i) => (
                <li key={item.id}
                  className={cn("flex items-center gap-2 rounded-md border px-2 py-1.5", atual?.id === item.id && "border-primary ring-1 ring-primary/30")}>
                  <input type="checkbox" checked={item.incluir} aria-label={`Incluir ${NOMES[item.tipo]}`}
                    onChange={(e) => mudar(item.id, { incluir: e.target.checked })} />
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setSelecionado(item.id)}>
                    <span className="block truncate text-sm font-medium">{item.titulo || NOMES[item.tipo]}</span>
                    {item.titulo && <span className="block text-xs text-muted-foreground">{NOMES[item.tipo]}</span>}
                  </button>
                  {item.notas && <StickyNote className="size-3.5 text-muted-foreground" aria-label="Tem notas" />}
                  <Button type="button" variant="ghost" size="icon-sm" aria-label={`Subir ${NOMES[item.tipo]}`} disabled={i === 0} onClick={() => mover(i, -1)}><ArrowUp /></Button>
                  <Button type="button" variant="ghost" size="icon-sm" aria-label={`Descer ${NOMES[item.tipo]}`} disabled={i === itens.length - 1} onClick={() => mover(i, 1)}><ArrowDown /></Button>
                  {item.tipo === "texto" && (
                    <Button type="button" variant="ghost" size="icon-sm" aria-label="Remover slide de texto"
                      onClick={() => setItens((l) => l.filter((x) => x.id !== item.id))}><Trash2 /></Button>
                  )}
                </li>
              ))}
            </ol>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" size="sm" onClick={adicionarTexto}><Plus /> Slide de texto</Button>
              <Button type="button" variant="ghost" size="sm" onClick={restaurar}>Voltar ao padrão</Button>
            </div>
          </div>

          {atual && (
            <div className="grid content-start gap-3">
              <Previa item={atual} projeto={projeto} />
              <label className="grid gap-1 text-sm">
                <span className="font-medium">Título do slide</span>
                <Input value={atual.titulo} maxLength={120}
                  placeholder={atual.tipo === "capa" ? projeto.nome_projeto : TITULO_PADRAO[atual.tipo]}
                  onChange={(e) => mudar(atual.id, { titulo: e.target.value })} />
              </label>
              {atual.tipo === "texto" && (
                <label className="grid gap-1 text-sm">
                  <span className="font-medium">Texto (um tópico por linha)</span>
                  <Textarea rows={5} maxLength={3000} value={atual.texto} onChange={(e) => mudar(atual.id, { texto: e.target.value })} />
                </label>
              )}
              <label className="grid gap-1 text-sm">
                <span className="font-medium">Notas do apresentador</span>
                <Textarea rows={3} maxLength={3000} value={atual.notas} placeholder="Aparecem só para quem apresenta"
                  onChange={(e) => mudar(atual.id, { notas: e.target.value })} />
              </label>
            </div>
          )}
        </div>

        <DialogFooter className="items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            {fmt.pluralizar(incluidos.length, "slide", "slides")} · slides sem conteúdo (ex.: orçamento num projeto sem orçamento) são pulados.
          </p>
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={aoFechar}>Fechar</Button>
            <Button type="button" carregando={gerando} disabled={incluidos.length === 0} onClick={gerar}>
              <Presentation /> {gerando ? "Gerando slides..." : "Gerar PowerPoint"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
