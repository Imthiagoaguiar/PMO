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
    # Log estruturado (JSON por linha) lido pelo vigia. Vazio = só no terminal.
    log_arquivo: Optional[str] = "logs/pmo.log"

    # Log do worker da fila (python -m app.trabalhador), também lido pelo vigia.
    log_arquivo_trabalhador: Optional[str] = "logs/trabalhador.log"

    # ---------- Backup (python -m app.backup) ----------
    # Pasta dos backups (vazio = backup desligado). NÃO é apagada pela limpeza do disco.
    backup_dir: Optional[str] = "backups"
    backup_retencao: int = Field(14, ge=1, le=365, description="Quantos backups manter")
    # Cópia em OUTRO disco/pasta sincronizada (um backup no mesmo disco do banco não protege contra o disco morrer).
    backup_copia_dir: Optional[str] = None
    # Conexão que pode criar bancos, só para o teste de restauração (o usuário do app, de propósito, não pode).
    backup_admin_url: Optional[str] = None
    backup_hora: str = Field("02:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    backup_max_horas: int = Field(26, ge=1, description="O vigia avisa se o último backup for mais velho que isso")
    pg_bin_dir: Optional[str] = None
    log_arquivo_backup: Optional[str] = "logs/backup.log"

    # ---------- Limpeza do disco (python -m app.limpeza) ----------
    # Abaixo deste espaço livre, a limpeza apaga o que é recriável (logs antigos, caches, registros velhos).
    limpeza_min_gb: float = Field(10, ge=1, le=10_000)
    limpeza_retencao_dias: int = Field(90, ge=7, le=3650)
    log_arquivo_limpeza: Optional[str] = "logs/limpeza.log"

    # Onde ficam os contadores de limite/bloqueio: "auto"/"redis" = no Redis, compartilhados entre os
    # processos da API (com volta para a memória se o Redis cair); "memoria" = em cada processo.
    limites_backend: Literal["auto", "redis", "memoria"] = "auto"

    # ---------- Fila de importação (Redis + RQ) ----------
    redis_url: str = "redis://localhost:6379/0"
    # true = o job roda na hora, dentro da própria requisição (testes; dispensa Redis e worker).
    fila_sincrona: bool = False
    importacao_max_mb: int = Field(20, ge=1, le=200)
    importacao_max_linhas: int = Field(100_000, ge=100, le=1_000_000)

    # ---------- Vigia (python -m app.vigia): procura erros e falhas a cada hora; nunca altera nada ----------
    # Quem pode ver os relatórios: e-mails de ADMINS, em JSON. Vazio = ninguém vê pela interface.
    vigia_emails: List[str] = []
    vigia_intervalo_minutos: int = Field(60, ge=5, le=24 * 60)
    vigia_api_url: str = "http://localhost:8000"  # para a sonda de saúde
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/pmo"
    # Conexões por processo da API (WEB_CONCURRENCY processos): fixas + extras. Some tudo e deixe folga
    # para o worker, o vigia e o backup dentro do max_connections do Postgres (padrão 100).
    db_pool_size: int = Field(10, ge=1, le=100)
    db_max_overflow: int = Field(10, ge=0, le=100)
    db_pool_timeout: int = Field(10, ge=1, le=120)  # segundos esperando conexão antes do 503
    # Requisições além do pool esperam numa fila (sem ocupar thread nem conexão); passando disto, 503.
    fila_espera_max_segundos: float = Field(15.0, ge=1, le=120)
    # Verificações de senha (Argon2, 64 MB de RAM cada) ao mesmo tempo, por processo. 300 logins juntos
    # sem esse teto levaram a API a 1,1 GB de memória no teste de carga.
    argon2_simultaneos: int = Field(4, ge=1, le=64)

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
    cookie_path: str = Field("/auth", pattern=r"^/[A-Za-z0-9/_-]*auth$")

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
    # Endereço do front usado nos links dos e-mails (redefinição de senha). Padrão: a 1ª de CORS_ORIGENS.
    frontend_url: Optional[str] = None
    redefinicao_senha_minutos: int = Field(30, ge=5, le=24 * 60)

    @property
    def url_do_front(self) -> str:
        return (self.frontend_url or self.cors_origens[0]).rstrip("/")

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
            if not self.url_do_front.startswith("https://"):
                raise ValueError("Em produção, o link dos e-mails (FRONTEND_URL ou a 1ª de CORS_ORIGENS) deve usar https://.")
        if self.clerk_emissor:
            self.clerk_emissor = self.clerk_emissor.rstrip("/")
            if not self.clerk_emissor.startswith("https://"):
                raise ValueError("CLERK_EMISSOR deve começar com https://")
        return self


settings = Settings()
