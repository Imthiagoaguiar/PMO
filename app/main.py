import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolEsgotado

from app import registro
from app.auditoria import ip_requisicao
from app.config import settings
from app.database import engine
from app.limites import KB, MB, LimiteCorpo, LimiteSimultaneas, RecusaNul
from app.routers import anexos, auditoria, auth, comentarios, dependencias, fases, importacoes, marcos, orcamento, pilares, projetos, tarefas, usuarios, vigia

EM_PRODUCAO = settings.ambiente == "prod"

registro.configurar(settings.log_arquivo)
logger = logging.getLogger("pmo")

# As tabelas são criadas/alteradas pelo Alembic: `alembic upgrade head`.
app = FastAPI(
    title="PMO Waterfall API",
    version="1.0.0",
    # A documentação interativa expõe todo o mapa da API: só fica ligada em dev.
    docs_url=None if EM_PRODUCAO else "/docs",
    redoc_url=None,
    openapi_url=None if EM_PRODUCAO else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origens,  # lista explícita; necessário para enviar o cookie
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["Content-Disposition", "Retry-After"],  # o front precisa ler o nome do .xlsx exportado
)


# JSON comum: 1 MB sobra (300 tarefas com descrição longa dão ~200 KB).
# Importação: IMPORTACAO_MAX_MB de planilha (20 MB) + a sobra do multipart.
# Ordem: o último adicionado é o mais externo. LimiteCorpo corta o tamanho ANTES de o RecusaNul ler o corpo.
app.add_middleware(RecusaNul)
app.add_middleware(
    LimiteCorpo, padrao=1 * MB, por_rota={"/projetos/importar": settings.importacao_max_mb * MB + 64 * KB},
    por_padrao={r"/projetos/\d+/tarefas/\d+/anexos": settings.anexos_max_mb * MB + 64 * KB},
)
# Fila de entrada: no máximo tantas requisições ao mesmo tempo quantas conexões o pool tem (ver
# app/limites.py). Fica DENTRO do registro, para os 503 de fila cheia aparecerem no log (e no vigia).
app.add_middleware(LimiteSimultaneas, maximo=settings.db_pool_size + settings.db_max_overflow,
                   espera_max=settings.fila_espera_max_segundos)
# Mais externo de todos: mede o tempo total e registra até as respostas dos outros middlewares (413, 422).
app.add_middleware(registro.RegistroDeAcesso)


@app.exception_handler(PoolEsgotado)
async def sistema_ocupado(request: Request, exc: PoolEsgotado) -> JSONResponse:
    """Todas as conexões do banco ocupadas por mais de DB_POOL_TIMEOUT: é sobrecarga, não defeito."""
    logger.warning("Banco sem conexão livre em %s %s (pool esgotado): respondendo 503", request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "O sistema está com muitos acessos agora. Tente de novo em alguns segundos."},
        headers={"Retry-After": "5"},
    )


@app.middleware("http")
async def contexto_e_headers(request: Request, call_next):
    # Disponibiliza o IP para a auditoria sem precisar passar o Request adiante.
    token = ip_requisicao.set(request.client.host if request.client else None)
    try:
        response = await call_next(request)
    finally:
        ip_requisicao.reset(token)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    if EM_PRODUCAO:
        # Em dev a página /docs precisa carregar scripts, por isso CSP e HSTS só em produção.
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/saude", tags=["Infra"], include_in_schema=not EM_PRODUCAO)
def saude() -> JSONResponse:
    """
    Health check do balanceador / orquestrador.
    - Banco fora: 503 (a API não serve nada sem ele).
    - Fila (Redis ou worker) fora: 200 "degradado" — o sistema funciona, só a importação de planilhas
      não; derrubar a API inteira por isso seria pior. O vigia transforma isso em alerta.
    """
    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check: banco indisponível")
        return JSONResponse({"status": "erro", "banco": "indisponivel"}, status_code=503)
    situacao_fila = _situacao_da_fila()
    return JSONResponse({"status": "ok" if situacao_fila in ("ok", "sincrona") else "degradado",
                         "banco": "ok", "fila": situacao_fila})


def _situacao_da_fila() -> str:
    if settings.fila_sincrona:
        return "sincrona"  # processa dentro da API: não há worker para conferir
    from rq import Worker

    from app import fila

    try:
        conexao = fila.conexao()
        conexao.ping()
        return "ok" if Worker.count(connection=conexao) > 0 else "sem_worker"
    except Exception:  # Redis fora do ar / URL errada: o detalhe vai para o log do vigia
        return "indisponivel"


app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(projetos.router)
app.include_router(tarefas.router)
app.include_router(tarefas.minhas_router)
app.include_router(comentarios.router)
app.include_router(pilares.router)
app.include_router(fases.router)
app.include_router(dependencias.router)
app.include_router(marcos.router)
app.include_router(orcamento.router)
app.include_router(anexos.router)
app.include_router(auditoria.router)
app.include_router(vigia.router)
app.include_router(importacoes.router)
