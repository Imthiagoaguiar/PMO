import { useMutation, useQueryClient } from "@tanstack/react-query"
import { FileSpreadsheet, PenLine, Plus, Sparkles, Trash2 } from "lucide-react"
import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { Campo, COR_FASE } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError, criarGeradorDeChave } from "@/lib/api"
import { notificarErro } from "@/lib/formato"
import { cn } from "@/lib/utils"
import type { ImportacaoResultado, Projeto, Sugestao } from "@/lib/tipos"

interface DadosBasicos {
  nome: string
  orcamento: string
  dataInicio: string
}

const VAZIO: DadosBasicos = { nome: "", orcamento: "", dataInicio: "" }

function CamposBasicos({ dados, aoMudar, dataObrigatoria = false }: {
  dados: DadosBasicos
  aoMudar: (d: DadosBasicos) => void
  dataObrigatoria?: boolean
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <Campo label="Nome do projeto" htmlFor="np-nome" className="sm:col-span-3">
        <Input id="np-nome" required minLength={3} maxLength={200} value={dados.nome} onChange={(e) => aoMudar({ ...dados, nome: e.target.value })} />
      </Campo>
      <Campo label="Orçamento aprovado (R$)" htmlFor="np-orcamento" className="sm:col-span-2">
        <Input
          id="np-orcamento"
          type="number"
          min={0}
          step="0.01"
          inputMode="decimal"
          placeholder="Opcional"
          value={dados.orcamento}
          onChange={(e) => aoMudar({ ...dados, orcamento: e.target.value })}
        />
      </Campo>
      <Campo label={dataObrigatoria ? "Data de início" : "Data de início (opcional)"} htmlFor="np-data">
        <Input id="np-data" type="date" required={dataObrigatoria} value={dados.dataInicio} onChange={(e) => aoMudar({ ...dados, dataInicio: e.target.value })} />
      </Campo>
    </div>
  )
}

function corpoBasico(d: DadosBasicos) {
  return {
    nome_projeto: d.nome.trim(),
    orcamento_aprovado: d.orcamento === "" ? null : d.orcamento,
    data_inicio: d.dataInicio || null,
  }
}

// ---------- Revisão da sugestão da IA ----------

interface TarefaEditavel {
  chave: number
  descricao: string
  duracao: string
}

interface FaseEditavel {
  fase: string
  tarefas: TarefaEditavel[]
}

let proximaChave = 1

function paraEditavel(s: Sugestao): FaseEditavel[] {
  return s.fases.map((f) => ({
    fase: f.fase,
    tarefas: f.tarefas.map((t) => ({
      chave: proximaChave++,
      descricao: t.descricao,
      duracao: t.duracao_dias_uteis ? String(t.duracao_dias_uteis) : "",
    })),
  }))
}

function RevisaoSugestao({ fases, aoMudar }: { fases: FaseEditavel[]; aoMudar: (f: FaseEditavel[]) => void }) {
  function alterar(iFase: number, mudar: (t: TarefaEditavel[]) => TarefaEditavel[]) {
    aoMudar(fases.map((f, i) => (i === iFase ? { ...f, tarefas: mudar(f.tarefas) } : f)))
  }

  return (
    <div className="grid gap-4">
      {fases.map((f, iFase) => (
        <div key={f.fase} className="rounded-lg border">
          <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-2">
            <div className="flex items-center gap-2 text-sm font-medium">
              <span className={cn("size-2.5 rounded-full", COR_FASE[iFase])} />
              {iFase + 1}. {f.fase}
              <span className="font-normal text-muted-foreground">({f.tarefas.length})</span>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={() => alterar(iFase, (ts) => [...ts, { chave: proximaChave++, descricao: "", duracao: "" }])}
            >
              <Plus /> Tarefa
            </Button>
          </div>
          <div className="grid gap-2 p-3">
            {f.tarefas.length === 0 && <p className="text-xs text-muted-foreground">Sem tarefas nesta fase.</p>}
            {f.tarefas.map((t) => (
              <div key={t.chave} className="flex items-center gap-2">
                <Input
                  aria-label="Descrição da tarefa"
                  className="h-8 flex-1"
                  value={t.descricao}
                  onChange={(e) => alterar(iFase, (ts) => ts.map((x) => (x.chave === t.chave ? { ...x, descricao: e.target.value } : x)))}
                />
                <Input
                  aria-label="Duração em dias úteis"
                  type="number"
                  min={1}
                  max={1000}
                  placeholder="dias"
                  className="h-8 w-20"
                  value={t.duracao}
                  onChange={(e) => alterar(iFase, (ts) => ts.map((x) => (x.chave === t.chave ? { ...x, duracao: e.target.value } : x)))}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Remover tarefa"
                  onClick={() => alterar(iFase, (ts) => ts.filter((x) => x.chave !== t.chave))}
                >
                  <Trash2 className="text-muted-foreground" />
                </Button>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------- Diálogo ----------

export function NovoProjeto({ aberto, aoFechar }: { aberto: boolean; aoFechar: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [aba, setAba] = useState("manual")
  const [dados, setDados] = useState<DadosBasicos>(VAZIO)
  const [arquivo, setArquivo] = useState<File | null>(null)
  const [errosPlanilha, setErrosPlanilha] = useState<string[]>([])
  const [texto, setTexto] = useState("")
  const [sugestao, setSugestao] = useState<{ fases: FaseEditavel[]; descartadas: number } | null>(null)
  // Mesma Idempotency-Key para o mesmo conteúdo: duplo clique ou nova tentativa não duplica o projeto.
  const [chaves] = useState(criarGeradorDeChave)

  function fechar() {
    chaves.renovar()
    setDados(VAZIO)
    setArquivo(null)
    setErrosPlanilha([])
    setTexto("")
    setSugestao(null)
    setAba("manual")
    aoFechar()
  }

  function concluir(projeto: Projeto, mensagem: string) {
    queryClient.invalidateQueries({ queryKey: ["projetos"] })
    toast.success(mensagem)
    fechar()
    navigate(`/projetos/${projeto.id}`)
  }

  const criarManual = useMutation({
    mutationFn: (fases?: FaseEditavel[]) => {
      const corpo = {
        ...corpoBasico(dados),
        fases: (fases ?? []).map((f) => ({
          fase: f.fase,
          tarefas: f.tarefas
            .filter((t) => t.descricao.trim())
            .map((t) => ({ descricao: t.descricao.trim(), duracao_dias_uteis: t.duracao ? Number(t.duracao) : null })),
        })),
      }
      const chave = chaves.para("manual:" + JSON.stringify(corpo))
      return api<Projeto>("/projetos/manual", { method: "POST", json: corpo, headers: { "Idempotency-Key": chave } })
    },
    onSuccess: (p) => concluir(p, "Projeto criado com as 5 fases do Waterfall."),
    onError: notificarErro,
  })

  const importar = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.append("arquivo", arquivo as File)
      const basico = corpoBasico(dados)
      form.append("nome_projeto", basico.nome_projeto)
      if (basico.orcamento_aprovado) form.append("orcamento_aprovado", basico.orcamento_aprovado)
      if (basico.data_inicio) form.append("data_inicio", basico.data_inicio)
      const planilha = arquivo as File
      const chave = chaves.para(
        "importar:" + JSON.stringify([basico, planilha.name, planilha.size, planilha.lastModified]),
      )
      return api<ImportacaoResultado>("/projetos/importar", { method: "POST", form, headers: { "Idempotency-Key": chave } })
    },
    onSuccess: (r) => concluir(r.projeto, `Projeto importado com ${r.total_tarefas_importadas} tarefas.`),
    onError: (erro) => {
      if (erro instanceof ApiError && erro.detalhes.length) setErrosPlanilha(erro.detalhes)
      else notificarErro(erro)
    },
  })

  const sugerir = useMutation({
    mutationFn: () => api<Sugestao>("/projetos/ia", { method: "POST", json: { texto } }),
    onSuccess: (s) => {
      setSugestao({ fases: paraEditavel(s), descartadas: s.descartadas })
      setDados((d) => ({ ...d, nome: d.nome || s.nome_projeto }))
    },
    onError: notificarErro,
  })

  const ocupado = criarManual.isPending || importar.isPending || sugerir.isPending
  const totalSugeridas = sugestao?.fases.reduce((n, f) => n + f.tarefas.filter((t) => t.descricao.trim()).length, 0) ?? 0
  const temDuracao = sugestao?.fases.some((f) => f.tarefas.some((t) => t.duracao)) ?? false

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (aba === "manual") criarManual.mutate(undefined)
    else if (aba === "planilha") {
      setErrosPlanilha([])
      importar.mutate()
    } else if (sugestao) criarManual.mutate(sugestao.fases)
    else sugerir.mutate()
  }

  const textoBotao =
    aba === "planilha"
      ? importar.isPending ? "Importando..." : "Importar"
      : aba === "ia" && !sugestao
        ? sugerir.isPending ? "Analisando o texto..." : "Gerar sugestão"
        : criarManual.isPending ? "Criando..." : aba === "ia" ? `Criar projeto com ${totalSugeridas} tarefas` : "Criar projeto"

  const desabilitado =
    ocupado ||
    (aba === "planilha" && (!arquivo || dados.nome.trim().length < 3)) ||
    (aba === "manual" && dados.nome.trim().length < 3) ||
    (aba === "ia" && !sugestao && texto.trim().length < 50) ||
    (aba === "ia" && !!sugestao && (dados.nome.trim().length < 3 || (temDuracao && !dados.dataInicio)))

  return (
    <Dialog open={aberto} onOpenChange={(abrir) => !abrir && !ocupado && fechar()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={enviar} className="grid gap-5">
          <DialogHeader>
            <DialogTitle>Novo projeto</DialogTitle>
            <DialogDescription>O projeto já nasce com as 5 fases do Waterfall e você como dono.</DialogDescription>
          </DialogHeader>

          <Tabs value={aba} onValueChange={setAba}>
            <TabsList className="w-full">
              <TabsTrigger value="manual" disabled={ocupado}>
                <PenLine /> Manual
              </TabsTrigger>
              <TabsTrigger value="planilha" disabled={ocupado}>
                <FileSpreadsheet /> Planilha
              </TabsTrigger>
              <TabsTrigger value="ia" disabled={ocupado}>
                <Sparkles /> Com IA
              </TabsTrigger>
            </TabsList>

            <TabsContent value="manual" className="pt-4">
              <CamposBasicos dados={dados} aoMudar={setDados} />
            </TabsContent>

            <TabsContent value="planilha" className="grid gap-4 pt-4">
              <CamposBasicos dados={dados} aoMudar={setDados} />
              <Campo
                label="Planilha (.xlsx ou .csv, até 5 MB)"
                htmlFor="np-arquivo"
                dica="Colunas: Fase e Descrição (obrigatórias); Data Início, Data Fim, Responsável e % Conclusão (opcionais). Um arquivo exportado pelo PMO também serve."
              >
                <Input
                  id="np-arquivo"
                  type="file"
                  accept=".xlsx,.csv"
                  onChange={(e) => {
                    setArquivo(e.target.files?.[0] ?? null)
                    setErrosPlanilha([])
                  }}
                />
              </Campo>
              {errosPlanilha.length > 0 && (
                <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                  <p className="mb-1 font-medium">Nada foi importado. Corrija a planilha:</p>
                  <ul className="list-disc space-y-0.5 pl-5">
                    {errosPlanilha.slice(0, 15).map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                  {errosPlanilha.length > 15 && <p className="mt-1">... e mais {errosPlanilha.length - 15} erros.</p>}
                </div>
              )}
            </TabsContent>

            <TabsContent value="ia" className="grid gap-4 pt-4">
              {!sugestao ? (
                <Campo
                  label="Termo de Abertura ou resumo do projeto"
                  htmlFor="np-texto"
                  dica={`${texto.trim().length} / 15.000 caracteres (mínimo 50). A IA sugere nome e tarefas; você revisa antes de criar.`}
                >
                  <Textarea
                    id="np-texto"
                    rows={10}
                    maxLength={15000}
                    placeholder="Cole aqui o objetivo, escopo, entregas, prazos e restrições do projeto..."
                    value={texto}
                    onChange={(e) => setTexto(e.target.value)}
                  />
                </Campo>
              ) : (
                <>
                  <CamposBasicos dados={dados} aoMudar={setDados} dataObrigatoria={temDuracao} />
                  <p className="text-xs text-muted-foreground">
                    Com a data de início, as tarefas com duração ganham datas em sequência, pulando fins de semana e feriados nacionais.
                    {sugestao.descartadas > 0 && ` ${sugestao.descartadas} sugestão(ões) fora do formato foram descartadas.`}
                  </p>
                  <RevisaoSugestao fases={sugestao.fases} aoMudar={(fases) => setSugestao({ ...sugestao, fases })} />
                  <Button type="button" variant="link" className="justify-self-start px-0" onClick={() => setSugestao(null)}>
                    Voltar e editar o texto
                  </Button>
                </>
              )}
            </TabsContent>
          </Tabs>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={fechar} disabled={ocupado}>
              Cancelar
            </Button>
            <Button type="submit" disabled={desabilitado}>
              {aba === "ia" && !sugestao && <Sparkles />}
              {textoBotao}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
