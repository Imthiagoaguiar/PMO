/**
 * Cliente da API.
 *
 * - O access token (JWT de 15 min) fica SÓ em memória: some ao recarregar a página, e nenhum
 *   script injetado consegue lê-lo do localStorage.
 * - O refresh token vive num cookie httpOnly (path=/auth) que o JavaScript não enxerga;
 *   POST /auth/refresh troca ele por um novo access token.
 * - Ao receber 401, tenta renovar UMA vez e repete a requisição.
 */

const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000"

export class ApiError extends Error {
  status: number
  detalhes: string[]

  constructor(status: number, mensagem: string, detalhes: string[] = []) {
    super(mensagem)
    this.status = status
    this.detalhes = detalhes
  }
}

let accessToken: string | null = null
let renovacaoEmAndamento: Promise<Renovacao> | null = null
let aoExpirarSessao: (() => void) | null = null

export function definirAoExpirarSessao(callback: () => void) {
  aoExpirarSessao = callback
}

/** "expirada": só um 401 de verdade. "falhou": 429, erro do servidor ou rede — a sessão pode estar viva. */
export type Renovacao = "ok" | "expirada" | "falhou"

const esperar = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function tentarRenovar(): Promise<Renovacao> {
  for (let tentativa = 1; tentativa <= 3; tentativa++) {
    let resposta: Response
    try {
      resposta = await fetch(`${API}/auth/refresh`, { method: "POST", credentials: "include" })
    } catch {
      return "falhou"
    }
    if (resposta.ok) {
      accessToken = (await resposta.json()).access_token
      return "ok"
    }
    if (resposta.status === 409) {
      // Outra aba acabou de renovar: o navegador já guardou o cookie novo. Basta repetir.
      await esperar(250 * tentativa)
      continue
    }
    if (resposta.status === 401) {
      accessToken = null
      return "expirada"
    }
    return "falhou"
  }
  return "falhou"
}

/**
 * Uma renovação por vez — nesta aba E entre abas. O back ROTACIONA o refresh token e trata o
 * reuso de um token antigo como roubo (derruba todas as sessões). Duas abas renovando juntas
 * mandariam o mesmo cookie; a trava do navegador (Web Locks) faz a segunda esperar a primeira
 * e já sair com o cookie novo.
 */
export function renovarSessao(): Promise<Renovacao> {
  if (!renovacaoEmAndamento) {
    const travas = typeof navigator !== "undefined" ? navigator.locks : undefined
    const execucao = travas ? travas.request("pmo-renovar-sessao", tentarRenovar) : tentarRenovar()
    renovacaoEmAndamento = execucao.finally(() => {
      renovacaoEmAndamento = null
    })
  }
  return renovacaoEmAndamento
}

interface ErroValidacao {
  loc?: (string | number)[]
  msg?: string
}

function extrairErro(status: number, corpo: unknown): ApiError {
  const detail = (corpo as { detail?: unknown } | null)?.detail

  if (typeof detail === "string") return new ApiError(status, detail)
  if (Array.isArray(detail)) {
    // 422 do Pydantic: [{loc: ["body", "campo"], msg: "..."}]
    const itens = (detail as ErroValidacao[]).map((e) => {
      const campo = (e.loc ?? []).filter((p) => p !== "body" && p !== "query").join(".")
      const msg = String(e.msg ?? "inválido").replace(/^Value error, /, "")
      return campo ? `${campo}: ${msg}` : msg
    })
    return new ApiError(status, "Verifique os dados informados.", itens)
  }
  if (detail && typeof detail === "object") {
    // Importação de planilha: {mensagem, erros: [...]}
    const d = detail as { mensagem?: string; erros?: string[] }
    return new ApiError(status, d.mensagem ?? "Requisição inválida.", d.erros ?? [])
  }

  const padrao: Record<number, string> = {
    401: "Sua sessão expirou. Entre novamente.",
    403: "Você não tem permissão para esta ação.",
    404: "Não encontrado.",
    413: "Arquivo grande demais.",
    429: "Muitas requisições. Aguarde um pouco.",
  }
  return new ApiError(status, padrao[status] ?? "Erro inesperado no servidor. Tente novamente.")
}

export interface Opcoes extends Omit<RequestInit, "body"> {
  json?: unknown
  form?: FormData | URLSearchParams
  /** Não tenta renovar a sessão num 401 (rotas de login/refresh). */
  semRenovar?: boolean
}

async function executar(caminho: string, opcoes: Opcoes = {}, repetida = false): Promise<Response> {
  const { json, form, semRenovar, headers: headersExtras, ...init } = opcoes
  const headers = new Headers(headersExtras)
  let body: BodyInit | undefined
  if (json !== undefined) {
    headers.set("Content-Type", "application/json")
    body = JSON.stringify(json)
  } else if (form) {
    body = form // o navegador define o Content-Type (multipart ou urlencoded)
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`)

  let resposta: Response
  try {
    resposta = await fetch(`${API}${caminho}`, {
      ...init,
      headers,
      body,
      // O cookie de sessão só é necessário (e só é enviado) nas rotas /auth.
      credentials: caminho.startsWith("/auth/") ? "include" : "same-origin",
    })
  } catch {
    throw new ApiError(0, "Não foi possível conectar à API. Verifique se ela está rodando.")
  }

  if (resposta.status === 401 && !semRenovar && !repetida) {
    const renovacao = await renovarSessao()
    if (renovacao === "ok") return executar(caminho, opcoes, true)
    if (renovacao === "expirada") aoExpirarSessao?.()
    // Falha passageira (limite, servidor, rede): NÃO desloga — a sessão pode continuar válida.
    else throw new ApiError(0, "Não foi possível renovar sua sessão agora. Tente de novo em instantes.")
  }
  if (!resposta.ok) {
    const corpo = await resposta.json().catch(() => null)
    throw extrairErro(resposta.status, corpo)
  }
  return resposta
}

export async function api<T = void>(caminho: string, opcoes?: Opcoes): Promise<T> {
  const resposta = await executar(caminho, opcoes)
  if (resposta.status === 204) return undefined as T
  return (await resposta.json()) as T
}

/** Baixa um arquivo autenticado (ex.: exportação .xlsx) e dispara o download no navegador. */
export async function baixarArquivo(caminho: string, nomePadrao: string): Promise<void> {
  const resposta = await executar(caminho)
  const disposicao = resposta.headers.get("Content-Disposition") ?? ""
  const nome = /filename="([^"]+)"/.exec(disposicao)?.[1] ?? nomePadrao
  const url = URL.createObjectURL(await resposta.blob())
  const link = Object.assign(document.createElement("a"), { href: url, download: nome })
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** Resultado da 1ª etapa: sessão aberta, ou a senha foi aceita e falta o código do 2FA. */
export type ResultadoLogin = { requer2fa: false } | { requer2fa: true; desafio: string }

export async function entrar(email: string, senha: string): Promise<ResultadoLogin> {
  const form = new URLSearchParams({ username: email, password: senha })
  const resposta = await api<{ access_token?: string; requer_2fa?: boolean; desafio?: string }>("/auth/login", {
    method: "POST",
    form,
    semRenovar: true,
  })
  if (resposta.requer_2fa && resposta.desafio) return { requer2fa: true, desafio: resposta.desafio }
  accessToken = resposta.access_token ?? null
  return { requer2fa: false }
}

export async function entrarComCodigo(desafio: string, codigo: string): Promise<void> {
  const resposta = await api<{ access_token: string }>("/auth/login/2fa", {
    method: "POST",
    json: { desafio, codigo },
    semRenovar: true,
  })
  accessToken = resposta.access_token
}

export async function sair(): Promise<void> {
  try {
    await api("/auth/logout", { method: "POST", semRenovar: true })
  } finally {
    accessToken = null
  }
}

export function esquecerSessaoLocal() {
  accessToken = null
}
