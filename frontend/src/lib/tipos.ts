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
  /** Até quando não recebe o e-mail de tarefas atrasadas (null = recebendo). */
  lembretes_silenciados_ate?: string | null
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
  /** Quando chegou a 100% (nulo se aberta). */
  concluida_em?: string | null
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
  /** true: a fase atual é a primeira, na ordem, com pendência; false: fases em paralelo. */
  seguir_ordem_fases: boolean
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
  /** true: orçado/realizado vêm da aba Orçamento (não se editam no pilar). */
  financeiro_do_orcamento: boolean
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

// ---------- Dependências, marcos e orçamento ----------

export type TipoDependencia = "inicio_apos_fim" | "fim_apos_fim"

export interface Dependencia {
  id: number
  predecessora_id: number
  sucessora_id: number
  tipo: TipoDependencia
}

export type FormaMarco = "pin" | "bandeira" | "circulo" | "quadrado" | "triangulo" | "losango" | "estrela" | "faixa"
export type CorMarco = "vermelho" | "laranja" | "amarelo" | "verde" | "azul" | "roxo" | "cinza"

export interface Marco {
  id: number
  titulo: string
  descricao: string | null
  data: string
  data_fim: string | null
  forma: FormaMarco
  cor: CorMarco
  versao: number
}

export type CategoriaOrcamento = "capex" | "opex"

export interface ItemOrcamento {
  id: number
  descricao: string
  categoria: CategoriaOrcamento
  pilar_id: number | null
  pilar_nome: string | null
  valor_orcado: string
  valor_realizado: string
  saldo: string
  execucao: number | null
  estourado: boolean
  lancamentos: number
  versao: number
}

export interface Lancamento {
  id: number
  item_id: number
  data: string
  descricao: string
  documento: string | null
  valor: string
  criado_por: UsuarioResumo | null
  criado_em: string
}

export interface TotalOrcamento {
  chave: string
  nome: string
  orcado: string
  realizado: string
}

export interface PontoCurva {
  mes: string
  realizado_no_mes: string
  realizado_acumulado: string
  planejado_acumulado: string | null
}

export interface ResumoOrcamento {
  orcamento_aprovado: string | null
  total_orcado: string
  total_realizado: string
  saldo: string
  execucao: number | null
  avanco_fisico: number
  previsao_final: string | null
  itens: ItemOrcamento[]
  por_categoria: TotalOrcamento[]
  por_pilar: TotalOrcamento[]
  curva: PontoCurva[]
  alertas: { nivel: "alto" | "medio" | "info"; mensagem: string }[]
}

// ---------- Anexos e gargalos ----------

export interface Anexo {
  id: number
  tarefa_id: number | null
  tarefa_descricao: string
  nome_original: string
  tamanho: number
  tipo_mime: string
  sha256: string
  enviado_por: UsuarioResumo | null
  enviado_em: string
}

export interface GrupoGargalo {
  nome: string
  tarefas_com_prazo: number
  entregues_no_prazo: number
  entregues_atrasadas: number
  abertas_atrasadas: number
  dias_atraso_entregues: number
  dias_atraso_abertas: number
  atraso_medio: number | null
  pior_atraso: number
  pontualidade: number | null
}

export interface TarefaGargalo {
  tarefa_id: number
  descricao: string
  fase: string
  pilar: string | null
  responsavel: string | null
  prazo: string
  dias_atraso: number
  situacao: "entregue_atrasada" | "aberta_atrasada"
}

export interface Gargalos {
  dias_atraso_total: number
  pontualidade: number | null
  entregues_sem_data: number
  por_fase: GrupoGargalo[]
  por_pilar: GrupoGargalo[]
  por_responsavel: GrupoGargalo[]
  piores: TarefaGargalo[]
}

// ---------- Curva S, feedback e slides ----------

export interface PontoCurvaS {
  data: string
  planejado_pct: number
  realizado_pct: number | null
  planejado_rs: string | null
  realizado_rs: string | null
}

export interface CurvaS {
  pontos: PontoCurvaS[]
  tarefas_sem_datas: number
  com_orcamento: boolean
  planejado_hoje: number | null
  realizado_hoje: number | null
  desvio_pp: number | null
  idp: number | null
  planejado_rs_hoje: string | null
  realizado_rs_hoje: string | null
}

export interface NumerosFeedback {
  tarefas: number
  entregues: number
  no_prazo: number
  antecipadas: number
  entregues_atrasadas: number
  atraso_medio: number | null
  pontualidade: number | null
  abertas: number
  abertas_vencidas: number
  maior_atraso_aberto: number
  reaberturas: number
  sem_prazo: number
}

export interface FeedbackEntidade {
  chave: string
  nome: string
  numeros: NumerosFeedback
  pontos_fortes: string[]
  pontos_a_melhorar: string[]
  observacao: string | null
}

export interface Feedback {
  pessoas: FeedbackEntidade[]
  areas: FeedbackEntidade[]
  pontualidade_projeto: number | null
  atraso_medio_projeto: number | null
}

export type TipoSlide = "capa" | "resumo" | "curva_s" | "linha_do_tempo" | "marcos" | "pilares" | "orcamento" | "gargalos" | "atencao" | "texto"

export interface ModeloApresentacao {
  id: number
  nome: string
  tamanho: number
  enviado_por: UsuarioResumo | null
  enviado_em: string
}
