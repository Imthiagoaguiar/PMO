// Espelho dos schemas Pydantic do back (app/schemas.py).

export type PapelUsuario = "admin" | "gerente" | "membro"
export type ProvedorAuth = "local" | "entra_id" | "google" | "clerk"
export type StatusProjeto = "Planejado" | "Em andamento" | "Concluído" | "Cancelado"
export type PapelProjeto = "editor" | "visualizador"

export const STATUS_PROJETO: StatusProjeto[] = ["Planejado", "Em andamento", "Concluído", "Cancelado"]
export const FASES_WATERFALL = ["Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento"] as const

export interface Pagina<T> {
  itens: T[]
  total: number
  limite: number
  offset: number
}

export interface UsuarioResumo {
  id: number
  nome: string
  email: string
}

export interface Usuario extends UsuarioResumo {
  papel: PapelUsuario
  ativo: boolean
  provedor_auth: ProvedorAuth
  aguardando_primeiro_login: boolean
  trocar_senha: boolean
  dois_fatores_ativo: boolean
  exige_configurar_2fa: boolean
  /** Admin listado em VIGIA_EMAILS: vê os relatórios do vigia. */
  pode_ver_vigia: boolean
}

export interface StatusDoisFatores {
  ativo: boolean
  obrigatorio: boolean
  codigos_restantes: number
}

export interface Tarefa {
  id: number
  fase_id: number
  descricao: string
  data_inicio: string | null
  data_fim: string | null
  responsavel: string | null
  responsavel_id: number | null
  pilar_id: number | null
  percentual_conclusao: number
  /** Bloqueio otimista: enviar de volta no PATCH (409 se outra pessoa salvou antes). */
  versao: number
}

export interface MinhaTarefa extends Tarefa {
  projeto_id: number
  nome_projeto: string
  fase_titulo: string
  atrasada: boolean
}

export interface Fase {
  id: number
  titulo: string
  ordem_sequencia: number
  /** As primeiras 200; o resto vem paginado de GET /projetos/{id}/tarefas. */
  tarefas: Tarefa[]
  total_tarefas: number
}

export interface ProjetoResumo {
  id: number
  nome_projeto: string
  orcamento_aprovado: string | null // Decimal chega como texto
  data_inicio: string | null
  status: StatusProjeto
  dono_id: number
  /** Bloqueio otimista: enviar de volta no PATCH (409 se outra pessoa salvou antes). */
  versao: number
}

export interface Permissoes {
  editar: boolean
  gerenciar: boolean
  comentar: boolean
}

export type FarolPilar = "verde" | "amarelo" | "vermelho"

export interface PilarResumo {
  id: number
  nome: string
  ordem: number
  farol: FarolPilar | null
}

/** Frente de trabalho (TI, Suprimentos, CAPEX, Pessoas...): andamento pelas tarefas + farol + financeiro. */
export interface Pilar extends PilarResumo {
  status_texto: string | null
  status_atualizado_em: string | null
  status_atualizado_por: { id: number; nome: string; email: string } | null
  valor_orcado: string | null // Decimal chega como texto
  valor_realizado: string | null
  desvio: string | null
  execucao_financeira: number | null
  total_tarefas: number
  tarefas_concluidas: number
  tarefas_atrasadas: number
  percentual_conclusao: number
  versao: number
}

export interface Projeto extends ProjetoResumo {
  fases: Fase[]
  pilares: PilarResumo[]
  permissoes: Permissoes | null
}

export interface FaseIndicadores {
  fase_id: number
  titulo: string
  ordem_sequencia: number
  total_tarefas: number
  tarefas_concluidas: number
  tarefas_atrasadas: number
  percentual_conclusao: number
}

export interface Indicadores {
  projeto_id: number
  percentual_conclusao: number
  total_tarefas: number
  tarefas_concluidas: number
  tarefas_atrasadas: number
  data_inicio_prevista: string | null
  data_fim_prevista: string | null
  fase_atual: string | null
  fases: FaseIndicadores[]
}

export interface Membro extends UsuarioResumo {
  papel: PapelProjeto
  dono: boolean
}

export interface Comentario {
  id: number
  projeto_id: number
  tarefa_id: number | null
  texto: string
  criado_em: string
  editado_em: string | null
  autor: UsuarioResumo | null
}

export interface EventoAuditoria {
  id: number
  criado_em: string
  usuario_id: number | null
  usuario: UsuarioResumo | null
  acao: string
  entidade: string
  entidade_id: number | null
  projeto_id: number | null
  detalhes: Record<string, unknown> | null
  ip: string | null
}

export interface TarefaSugerida {
  descricao: string
  duracao_dias_uteis: number | null
}

export interface Sugestao {
  nome_projeto: string
  fases: { fase: string; tarefas: TarefaSugerida[] }[]
  descartadas: number
  modelo: string
}

export type StatusImportacao = "na_fila" | "processando" | "concluida" | "falhou"

/** Importação de planilha processada em segundo plano (fila). */
export interface Importacao {
  id: number
  status: StatusImportacao
  progresso: number
  etapa: string | null
  nome_projeto: string
  arquivo_nome: string
  arquivo_bytes: number
  total_linhas: number | null
  projeto_id: number | null
  mensagem: string | null
  erros: string[]
  criada_em: string
  iniciada_em: string | null
  concluida_em: string | null
}

// ---------- Vigia ----------

export type GravidadeAchado = "critica" | "alta" | "media" | "baixa"

export interface AchadoVigia {
  codigo: string | null
  titulo: string
  gravidade: GravidadeAchado
  evidencias: string[]
  causa_provavel: string | null
  impacto: string | null
  como_investigar: string[]
  origem: "regra" | "ia"
}

export interface VigiaRodadaResumo {
  id: number
  iniciada_em: string
  concluida_em: string
  janela_inicio: string
  pior_gravidade: GravidadeAchado | null
  resumo: string
  total_achados: number
  ia_situacao: string
}

export interface VigiaRodada extends VigiaRodadaResumo {
  achados: AchadoVigia[]
  sinais: Record<string, unknown>
  ia_modelo: string | null
}

export interface VigiaSituacao {
  intervalo_minutos: number
  ultima_rodada_em: string | null
  atrasado: boolean
}
