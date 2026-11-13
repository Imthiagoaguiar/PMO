"""
Limite de tamanho do corpo das requisições.

Sem isso, um JSON de centenas de MB seria lido inteiro na memória antes de qualquer validação.
O limite vale também para envios em partes (chunked, sem Content-Length): os bytes são
contados conforme chegam e a leitura é interrompida ao passar do limite.
Em produção, configure o mesmo limite no proxy reverso (ex.: nginx `client_max_body_size`).
"""

from typing import Dict

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

KB = 1024
MB = 1024 * KB


class LimiteCorpo:
    def __init__(self, app: ASGIApp, padrao: int, por_rota: Dict[str, int]) -> None:
        self.app = app
        self.padrao = padrao
        self.por_rota = por_rota

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limite = self.por_rota.get(scope["path"], self.padrao)
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
        return f"Requisição maior que o permitido ({limite // KB} KB)."

    @staticmethod
    async def _responder(scope: Scope, receive: Receive, send: Send, status: int, detalhe: str) -> None:
        await JSONResponse({"detail": detalhe}, status_code=status)(scope, receive, send)
