import { useMutation } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { Campo } from "@/components/comum"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api, ehConflitoDeVersao } from "@/lib/api"
import { useAtualizarProjeto } from "@/lib/consultas"
import { notificarErro } from "@/lib/formato"
import type { Fase, Membro, Tarefa } from "@/lib/tipos"

const SEM_RESPONSAVEL = "nenhum"
const TEXTO_LIVRE = "texto"

interface Props {
  projetoId: number
  fases: Fase[]
  membros: Membro[]
  /** Fase onde a tarefa nova será criada. */
  faseInicial: Fase
  /** Se informada, edita esta tarefa. */
  tarefa?: Tarefa
  aoFechar: () => void
}

export function TarefaDialog({ projetoId, fases, membros, faseInicial, tarefa, aoFechar }: Props) {
  const atualizar = useAtualizarProjeto(projetoId)
  const [descricao, setDescricao] = useState(tarefa?.descricao ?? "")
  const [faseId, setFaseId] = useState(String(tarefa?.fase_id ?? faseInicial.id))
  const [dataInicio, setDataInicio] = useState(tarefa?.data_inicio ?? "")
  const [modoFim, setModoFim] = useState<"data" | "duracao">("data")
  const [dataFim, setDataFim] = useState(tarefa?.data_fim ?? "")
  const [duracao, setDuracao] = useState("")
  const [percentual, setPercentual] = useState(String(tarefa?.percentual_conclusao ?? 0))
  const [responsavel, setResponsavel] = useState(() => {
    if (tarefa?.responsavel_id) return String(tarefa.responsavel_id)
    return tarefa?.responsavel ? TEXTO_LIVRE : SEM_RESPONSAVEL
  })
  const [responsavelTexto, setResponsavelTexto] = useState(tarefa?.responsavel ?? "")

  function corpo(): Record<string, unknown> {
    const dados: Record<string, unknown> = {
      descricao: descricao.trim(),
      data_inicio: dataInicio || null,
      percentual_conclusao: Number(percentual),
      responsavel_id: responsavel !== SEM_RESPONSAVEL && responsavel !== TEXTO_LIVRE ? Number(responsavel) : null,
      responsavel: responsavel === TEXTO_LIVRE ? responsavelTexto.trim() || null : null,
    }
    if (modoFim === "duracao" && duracao) dados.duracao_dias_uteis = Number(duracao)
    else dados.data_fim = dataFim || null
    if (tarefa) {
      dados.fase_id = Number(faseId)
      // PATCH só com o que mudou (o histórico fica limpo).
      const original: Record<string, unknown> = { ...tarefa }
      for (const campo of Object.keys(dados)) {
        if (campo !== "duracao_dias_uteis" && dados[campo] === original[campo]) delete dados[campo]
      }
      // A versão que estava na tela: se alguém salvou depois, o back responde 409 em vez de sobrescrever.
      dados.versao = tarefa.versao
    }
    return dados
  }

  const salvar = useMutation({
    mutationFn: () => {
      const dados = corpo()
      if (tarefa) return api<Tarefa>(`/projetos/${projetoId}/tarefas/${tarefa.id}`, { method: "PATCH", json: dados })
      return api<Tarefa>(`/projetos/${projetoId}/fases/${faseId}/tarefas`, { method: "POST", json: dados })
    },
    onSuccess: (salva) => {
      atualizar()
      toast.success(tarefa ? "Tarefa atualizada." : "Tarefa criada.", {
        description: modoFim === "duracao" && salva.data_fim ? `Término calculado: ${salva.data_fim.split("-").reverse().join("/")}` : undefined,
      })
      aoFechar()
    },
    onError: (erro) => {
      notificarErro(erro)
      if (ehConflitoDeVersao(erro)) {
        atualizar() // traz a versão atual; a pessoa revisa e salva de novo
        aoFechar()
      }
    },
  })

  const datasInvalidas = modoFim === "data" && !!dataInicio && !!dataFim && dataFim < dataInicio
  const duracaoSemInicio = modoFim === "duracao" && !!duracao && !dataInicio

  function enviar(e: FormEvent) {
    e.preventDefault()
    if (!datasInvalidas && !duracaoSemInicio) salvar.mutate()
  }

  return (
    <Dialog open onOpenChange={(abrir) => !abrir && aoFechar()}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={enviar} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{tarefa ? "Editar tarefa" : "Nova tarefa"}</DialogTitle>
            <DialogDescription>{tarefa ? "Altere os campos e salve." : `Fase: ${faseInicial.titulo}`}</DialogDescription>
          </DialogHeader>

          <Campo label="Descrição" htmlFor="t-descricao">
            <Input id="t-descricao" required maxLength={500} autoFocus value={descricao} onChange={(e) => setDescricao(e.target.value)} />
          </Campo>

          {tarefa && (
            <Campo label="Fase">
              <Select value={faseId} onValueChange={setFaseId}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {fases.map((f) => (
                    <SelectItem key={f.id} value={String(f.id)}>
                      {f.ordem_sequencia}. {f.titulo}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Campo>
          )}

          <div className="grid grid-cols-2 gap-4">
            <Campo label="Início" htmlFor="t-inicio">
              <Input id="t-inicio" type="date" value={dataInicio} onChange={(e) => setDataInicio(e.target.value)} />
            </Campo>
            {modoFim === "data" ? (
              <Campo label="Término" htmlFor="t-fim">
                <Input id="t-fim" type="date" aria-invalid={datasInvalidas} value={dataFim} onChange={(e) => setDataFim(e.target.value)} />
              </Campo>
            ) : (
              <Campo label="Duração (dias úteis)" htmlFor="t-duracao">
                <Input id="t-duracao" type="number" min={1} max={1000} value={duracao} onChange={(e) => setDuracao(e.target.value)} />
              </Campo>
            )}
          </div>
          <div className="-mt-2 flex flex-wrap items-center gap-x-3 text-xs">
            <button
              type="button"
              className="text-primary hover:underline"
              onClick={() => setModoFim(modoFim === "data" ? "duracao" : "data")}
            >
              {modoFim === "data" ? "Calcular o término por dias úteis" : "Informar a data de término"}
            </button>
            {modoFim === "duracao" && <span className="text-muted-foreground">Pula fins de semana e feriados nacionais.</span>}
            {datasInvalidas && <span className="text-destructive">O término não pode ser antes do início.</span>}
            {duracaoSemInicio && <span className="text-destructive">Informe o início para calcular.</span>}
          </div>

          <div className="grid grid-cols-[1fr_auto] gap-4">
            <Campo label="Responsável">
              <Select value={responsavel} onValueChange={setResponsavel}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={SEM_RESPONSAVEL}>Sem responsável</SelectItem>
                  {membros.map((m) => (
                    <SelectItem key={m.id} value={String(m.id)}>
                      {m.nome}
                    </SelectItem>
                  ))}
                  <SelectItem value={TEXTO_LIVRE}>Outra pessoa (sem conta)...</SelectItem>
                </SelectContent>
              </Select>
              {responsavel === TEXTO_LIVRE && (
                <Input
                  aria-label="Nome do responsável"
                  placeholder="Nome ou fornecedor"
                  maxLength={150}
                  value={responsavelTexto}
                  onChange={(e) => setResponsavelTexto(e.target.value)}
                />
              )}
            </Campo>
            <Campo label="% concluído" htmlFor="t-percentual">
              <Input
                id="t-percentual"
                type="number"
                min={0}
                max={100}
                className="w-24"
                value={percentual}
                onChange={(e) => setPercentual(e.target.value)}
              />
            </Campo>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={aoFechar}>
              Cancelar
            </Button>
            <Button type="submit" disabled={salvar.isPending || !descricao.trim()}>
              {salvar.isPending ? "Salvando..." : "Salvar"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
