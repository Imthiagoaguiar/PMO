"""
Limite de tamanho do corpo das requisições.

Sem isso, um JSON de centenas de MB seria lido inteiro na memória antes de qualquer validação.
O limite vale também para envios em partes (chunked, sem Content-Length): os bytes são
contados conforme chegam e a leitura é interrompida ao passar do limite.
Em produção, configure o mesmo limite no proxy reverso (ex.: nginx `client_max_body_size`).
"""

import json
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

KB = 1024
MB = 1024 * KB


class LimiteCorpo:
    """`por_rota`: caminho exato -> limite; `por_padrao`: expressão regular do caminho -> limite (rotas com ids)."""

    def __init__(self, app: ASGIApp, padrao: int, por_rota: Dict[str, int],
                 por_padrao: Optional[Dict[str, int]] = None) -> None:
        self.app = app
        self.padrao = padrao
        self.por_rota = por_rota
        self.por_padrao = [(re.compile(f"^{regex}$"), limite) for regex, limite in (por_padrao or {}).items()]

    def _limite(self, caminho: str) -> int:
        if caminho in self.por_rota:
            return self.por_rota[caminho]
        return next((limite for regex, limite in self.por_padrao if regex.match(caminho)), self.padrao)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limite = self._limite(scope["path"])
        cabecalhos = dict(scope.get("headers") or [])
        declarado = cabecalhos.get(b"content-length")
        if declarado is not None:
            try:
                excede = int(declarado) > limite
            except ValueError:
                await self._responder(scope, receive, send, 400, "Content-Length inválido.")
                return
            if excede:
                await self._responder(scope, receive, send, 413, self._mensagem(limite))
                return

        recebidos = 0

        async def receive_contando() -> Message:
            nonlocal recebidos
            mensagem = await receive()
            if mensagem["type"] == "http.request":
                recebidos += len(mensagem.get("body", b""))
                if recebidos > limite:
                    # HTTPException (e não uma exceção própria): o FastAPI repassa HTTPException
                    # levantada durante a leitura do corpo; qualquer outra ele transforma em 400.
                    raise HTTPException(status_code=413, detail=self._mensagem(limite))
            return mensagem

        await self.app(scope, receive_contando, send)

    @staticmethod
    def _mensagem(limite: int) -> str:
        tamanho = f"{limite // MB} MB" if limite >= MB else f"{limite // KB} KB"  # "20 MB", não "20544 KB"
        return f"Requisição maior que o permitido ({tamanho})."

    @staticmethod
    async def _responder(scope: Scope, receive: Receive, send: Send, status: int, detalhe: str) -> None:
        await JSONResponse({"detail": detalhe}, status_code=status)(scope, receive, send)


class RecusaNul:
    """
    Recusa (422) texto com o caractere NUL (\\x00) no JSON, em formulários e na URL.

    O Postgres não grava NUL em colunas de texto e o driver levanta um ValueError que não é erro
    do SQLAlchemy: virava 500 em qualquer rota (o SQLite aceita, então só aparecia em produção).
    Upload multipart não passa por aqui (arquivo binário tem NUL legítimo): as células da
    planilha são limpas na importação e os campos do formulário são validados pelos schemas.
    """

    MENSAGEM = "Texto com caractere inválido (NUL). Remova-o e tente de novo."
    TIPOS = (b"application/json", b"application/x-www-form-urlencoded")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._tem_nul_no_formulario(scope.get("query_string", b"")):
            await LimiteCorpo._responder(scope, receive, send, 422, self.MENSAGEM)
            return

        cabecalhos = dict(scope.get("headers") or [])
        tipo = cabecalhos.get(b"content-type", b"").split(b";")[0].strip().lower()
        if tipo not in self.TIPOS:
            await self.app(scope, receive, send)
            return

        # O corpo já vem limitado pelo LimiteCorpo (1 MB): dá para ler inteiro e devolver depois.
        partes = []
        try:
            while True:
                mensagem = await receive()
                if mensagem["type"] != "http.request":
                    break
                partes.append(mensagem.get("body", b""))
                if not mensagem.get("more_body", False):
                    break
        except HTTPException as exc:  # 413 do LimiteCorpo durante a leitura
            await LimiteCorpo._responder(scope, receive, send, exc.status_code, exc.detail)
            return
        corpo = b"".join(partes)

        tem_nul = self._tem_nul_no_json(corpo) if tipo == b"application/json" else self._tem_nul_no_formulario(corpo)
        if tem_nul:
            await LimiteCorpo._responder(scope, receive, send, 422, self.MENSAGEM)
            return

        entregue = False

        async def receive_de_novo() -> Message:
            nonlocal entregue
            if not entregue:
                entregue = True
                return {"type": "http.request", "body": corpo, "more_body": False}
            return await receive()

        await self.app(scope, receive_de_novo, send)

    @staticmethod
    def _tem_nul_no_formulario(bruto: bytes) -> bool:
        texto = bruto.decode("utf-8", "replace")
        return "\x00" in texto or any("\x00" in k or "\x00" in v for k, v in parse_qsl(texto, keep_blank_values=True))

    @classmethod
    def _tem_nul_no_json(cls, corpo: bytes) -> bool:
        if b"\x00" in corpo:
            return True
        if b"\\u0000" not in corpo:  # atalho: sem o escape, não há NUL no JSON
            return False
        try:
            return cls._contem_nul(json.loads(corpo))
        except ValueError:
            return False  # JSON inválido: o FastAPI responde 422 com a mensagem dele

    @classmethod
    def _contem_nul(cls, valor: Any) -> bool:
        if isinstance(valor, str):
            return "\x00" in valor
        if isinstance(valor, dict):
            return any(cls._contem_nul(k) or cls._contem_nul(v) for k, v in valor.items())
        if isinstance(valor, list):
            return any(cls._contem_nul(v) for v in valor)
        return False


class LimiteSimultaneas:
    """
    No máximo `maximo` requisições sendo atendidas ao mesmo tempo neste processo; as outras esperam a
    vez numa fila (barata: não ocupam thread nem conexão). Quem espera mais que `espera_max` segundos
    recebe 503 com Retry-After.

    Por quê (teste de carga de 16/11/2026, 300 usuários): sem esse teto, o servidor aceitava tudo, cada
    requisição pegava uma conexão do banco na checagem do login e ficava com ela parada esperando
    thread para a rota; as threads, por sua vez, esperavam conexão. Com `maximo` = conexões do pool,
    o pool nunca esgota e ninguém segura conexão sem usar.
    """

    LIVRES = ("/saude",)  # o health check não entra na fila: sob carga, ele responde se o sistema vive

    def __init__(self, app: ASGIApp, maximo: int, espera_max: float) -> None:
        self.app = app
        self.maximo = maximo
        self.espera_max = espera_max
        self._vagas: Any = None  # asyncio.Semaphore, criado no laço de eventos do processo

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.LIVRES:
            await self.app(scope, receive, send)
            return
        import asyncio

        if self._vagas is None:
            self._vagas = asyncio.Semaphore(self.maximo)
        try:
            await asyncio.wait_for(self._vagas.acquire(), timeout=self.espera_max)
        except TimeoutError:
            resposta = JSONResponse(
                {"detail": "O sistema está com muitos acessos agora. Tente de novo em alguns segundos."},
                status_code=503, headers={"Retry-After": "5"},
            )
            await resposta(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self._vagas.release()
