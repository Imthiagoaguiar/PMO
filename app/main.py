import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.auditoria import ip_requisicao
from app.config import settings
from app.database import engine
from app.limites import KB, MB, LimiteCorpo
from app.routers import auditoria, auth, comentarios, projetos, tarefas, usuarios

EM_PRODUCAO = settings.ambiente == "prod"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
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
# Importação: 5 MB de planilha + a sobra do multipart.
app.add_middleware(LimiteCorpo, padrao=1 * MB, por_rota={"/projetos/importar": 5 * MB + 64 * KB})


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
    """Health check para load balancer / orquestrador: verifica se a API alcança o banco."""
    try:
        with engine.connect() as conexao:
            conexao.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check: banco indisponível")
        return JSONResponse({"status": "erro", "banco": "indisponivel"}, status_code=503)
    return JSONResponse({"status": "ok", "banco": "ok"})


app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(projetos.router)
app.include_router(tarefas.router)
app.include_router(tarefas.minhas_router)
app.include_router(comentarios.router)
app.include_router(auditoria.router)
