import type { EventoAuditoria } from "@/lib/tipos"

export const ACOES: Record<string, string> = {
  "projeto.criado": "criou o projeto",
  "projeto.importado": "importou o projeto de uma planilha",
  "projeto.atualizado": "atualizou o projeto",
  "projeto.exportado": "exportou o projeto para Excel",
  "projeto.excluido": "excluiu o projeto",
  "projeto.sugestao_ia": "pediu uma sugestão de escopo à IA",
  "tarefa.criada": "criou uma tarefa",
  "tarefa.atualizada": "atualizou uma tarefa",
  "tarefa.excluida": "excluiu uma tarefa",
  "membro.adicionado": "adicionou um membro",
  "membro.removido": "removeu um membro",
  "membro.papel_alterado": "alterou o papel de um membro",
  "comentario.criado": "comentou",
  "comentario.editado": "editou um comentário",
  "comentario.excluido": "excluiu um comentário",
  "auth.login": "entrou no sistema",
  "auth.logout": "saiu do sistema",
  "auth.login_falhou": "errou a senha",
  "auth.conta_bloqueada": "teve a conta bloqueada por tentativas",
  "auth.senha_alterada": "alterou a senha",
  "auth.reuso_refresh_token": "teve as sessões revogadas (reuso de token)",
  "usuario.criado": "criou um usuário",
  "usuario.atualizado": "atualizou um usuário",
  "usuario.criado_via_clerk": "entrou pela primeira vez pelo Clerk",
  "usuario.vinculado_clerk": "vinculou a conta ao Clerk",
}

const CAMPOS: Record<string, string> = {
  nome_projeto: "nome",
  orcamento_aprovado: "orçamento",
  data_inicio: "início",
  data_fim: "término",
  status: "status",
  descricao: "descrição",
  percentual_conclusao: "% concluído",
  responsavel: "responsável",
  responsavel_id: "responsável (usuário)",
  fase_id: "fase",
  papel: "papel",
  ativo: "ativo",
  nome: "nome",
  email: "e-mail",
  fase: "fase",
  tarefas: "tarefas",
  modo: "modo",
  arquivo: "arquivo",
  modelo: "modelo",
  caracteres: "caracteres",
  tarefa_id: "tarefa",
  autor_id: "autor",
  provedor: "provedor",
}

function valor(v: unknown): string {
  if (v === null || v === undefined || v === "") return "vazio"
  if (typeof v === "boolean") return v ? "sim" : "não"
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return v.split("-").reverse().join("/")
  return String(v)
}

/** {"percentual_conclusao": [0, 30]} -> ["% concluído: 0 → 30"] */
export function descreverDetalhes(evento: EventoAuditoria): string[] {
  if (!evento.detalhes) return []
  return Object.entries(evento.detalhes)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([campo, v]) => {
      const nome = CAMPOS[campo] ?? campo
      if (Array.isArray(v) && v.length === 2) return `${nome}: ${valor(v[0])} → ${valor(v[1])}`
      return `${nome}: ${valor(v)}`
    })
}

export function descreverAcao(acao: string) {
  return ACOES[acao] ?? acao
}
