from typing import List, Literal, Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida de variáveis de ambiente (ou do arquivo .env). Nomes não diferenciam maiúsculas."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ambiente: Literal["dev", "prod"] = "dev"
    # Fuso de quem usa o sistema: define o que é "hoje" (tarefa atrasada). O servidor pode estar em UTC.
    fuso_horario: str = "America/Sao_Paulo"
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

    # Bloqueio em duas camadas (a resposta é sempre a mesma: não revela se o e-mail existe):
    # - e-mail + IP: barra quem chuta senhas de um lugar, sem travar o dono da conta em outro;
    # - conta: barra ataque distribuído (muitos IPs) contra a mesma conta.
    max_tentativas_login: int = 5
    max_tentativas_conta: int = 20
    bloqueio_minutos: int = 15
    # Password spraying: falhas de login de QUALQUER conta vindas do mesmo IP, na janela de bloqueio.
    # Alto o bastante para um escritório inteiro (NAT) errar a senha de vez em quando.
    max_falhas_por_ip: int = 30
    # Teto de requisições de login por IP por minuto: só contra sobrecarga (o Argon2 é caro).
    # Logins certos contam aqui, então dimensione para o maior escritório atrás de um único IP.
    limite_logins_por_ip_minuto: int = Field(60, ge=10)
    # Duas abas renovando a sessão juntas mandam o mesmo refresh token: dentro desta janela o
    # reuso é tratado como corrida entre abas (409, tente de novo), não como roubo.
    janela_renovacao_paralela_segundos: int = Field(30, ge=0, le=120)

    # Consulta senhas novas na base de vazamentos do Have I Been Pwned (só os 5 primeiros
    # caracteres do hash SHA-1 saem do servidor; a senha nunca é enviada).
    verificar_senhas_vazadas: bool = True

    # Verificação em duas etapas (TOTP). Admins com login local são obrigados a ativar.
    exigir_2fa_admin: bool = True
    # Chave Fernet que cifra os segredos TOTP no banco. Gere com:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Separada da SECRET_KEY de propósito: trocar a SECRET_KEY não pode apagar o 2FA de todos.
    # Em dev, se vazia, é derivada da SECRET_KEY; em produção é obrigatória.
    chave_2fa: Optional[str] = None

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

    @field_validator("fuso_horario")
    @classmethod
    def _fuso_existe(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"fuso horário desconhecido: {v!r} (use um nome IANA, ex.: America/Sao_Paulo)")
        return v

    @model_validator(mode="after")
    def _validar_producao(self) -> "Settings":
        if self.ambiente == "prod":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE deve ser true em produção.")
            if any(o == "*" for o in self.cors_origens):
                raise ValueError("CORS_ORIGENS não pode conter '*' em produção.")
            if not self.chave_2fa:
                raise ValueError("CHAVE_2FA é obrigatória em produção.")
        if self.clerk_emissor:
            self.clerk_emissor = self.clerk_emissor.rstrip("/")
            if not self.clerk_emissor.startswith("https://"):
                raise ValueError("CLERK_EMISSOR deve começar com https://")
        return self


settings = Settings()
