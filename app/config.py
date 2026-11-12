from typing import List, Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida de variáveis de ambiente (ou do arquivo .env). Nomes não diferenciam maiúsculas."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ambiente: Literal["dev", "prod"] = "dev"
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/pmo"

    # Sem valor padrão de propósito: a API não sobe sem uma chave definida.
    # Gere com: python -c "import secrets; print(secrets.token_urlsafe(64))"
    secret_key: str = Field(..., min_length=32)
    jwt_algoritmo: Literal["HS256"] = "HS256"
    jwt_emissor: str = "pmo-api"
    access_token_minutos: int = Field(15, ge=1, le=60)
    refresh_token_dias: int = Field(7, ge=1, le=30)

    # Origens do front React autorizadas (JSON no .env: CORS_ORIGENS=["https://pmo.empresa.com"]).
    cors_origens: List[str] = ["http://localhost:5173"]
    cookie_secure: bool = True

    max_tentativas_login: int = 5
    bloqueio_minutos: int = 15

    # ---------- Integrações externas (todas opcionais: sem a chave, a integração fica desligada) ----------

    # Clerk: "Frontend API URL" da instância (Dashboard > API keys), ex.: https://xxx.clerk.accounts.dev
    clerk_emissor: Optional[str] = None
    clerk_jwks_url: Optional[str] = None  # padrão: <clerk_emissor>/.well-known/jwks.json
    clerk_partes_autorizadas: Optional[List[str]] = None  # claim "azp"; padrão: cors_origens
    # Primeiro login via Clerk cria o usuário local como "membro" (não vê nenhum projeto até ser adicionado).
    clerk_cadastro_automatico: bool = True

    brasil_api_url: str = "https://brasilapi.com.br/api/feriados/v1"

    groq_api_key: Optional[str] = None
    # Os modelos Llama 3 da Groq saíram dos planos free/developer em 16/08/2026.
    # Com contrato enterprise dá para usar GROQ_MODELO=llama-3.3-70b-versatile.
    groq_modelo: str = "openai/gpt-oss-120b"
    groq_timeout_segundos: float = 30.0

    resend_api_key: Optional[str] = None
    # Em teste, o Resend só entrega a partir de onboarding@resend.dev (e só para o e-mail da sua conta).
    email_remetente: str = "PMO <onboarding@resend.dev>"

    @model_validator(mode="after")
    def _validar_producao(self) -> "Settings":
        if self.ambiente == "prod":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE deve ser true em produção.")
            if any(o == "*" for o in self.cors_origens):
                raise ValueError("CORS_ORIGENS não pode conter '*' em produção.")
        if self.clerk_emissor:
            self.clerk_emissor = self.clerk_emissor.rstrip("/")
            if not self.clerk_emissor.startswith("https://"):
                raise ValueError("CLERK_EMISSOR deve começar com https://")
        return self


settings = Settings()
