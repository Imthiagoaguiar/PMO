"""
Comandos administrativos.

    python -m app.cli criar-admin --email admin@empresa.com.br --nome "Administrador"
    python -m app.cli limpar-tokens
    python -m app.cli redefinir-senha --email admin@empresa.com.br [--desligar-2fa]

`criar-admin` pede a senha no terminal (não fica no histórico do shell).
`redefinir-senha` é a saída para quem esqueceu a senha e não recebe o e-mail de "Esqueci minha senha" (ex.: o único
admin): pede a nova senha no terminal, desbloqueia a conta e derruba as sessões abertas. `--desligar-2fa` também
desliga a verificação em duas etapas (celular perdido); um admin terá de ativá-la de novo ao entrar.
`limpar-tokens` apaga refresh tokens vencidos ou revogados — agende para rodar 1x por dia (cron).
"""

import argparse
import getpass
import sys
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import delete, or_, select

from app import auditoria, dois_fatores, senhas, services
from app.database import SessionLocal
from app.models import PapelUsuario, ProvedorAuth, RefreshToken, Usuario
from app.schemas import SENHA_MAX, SENHA_MIN, UsuarioCreate
from app.security import agora_utc, gerar_hash_senha

# Tokens revogados são mantidos por um tempo para a detecção de reuso continuar funcionando.
RETENCAO_REVOGADOS = timedelta(days=30)


def criar_admin(email: str, nome: str) -> None:
    senha = getpass.getpass("Senha (mín. 12 caracteres): ")
    if senha != getpass.getpass("Confirme a senha: "):
        sys.exit("As senhas não conferem.")

    try:
        dados = UsuarioCreate(email=email, nome=nome, senha=senha, papel=PapelUsuario.ADMIN)
    except ValidationError as exc:
        sys.exit(f"Dados inválidos:\n{exc}")

    try:
        senhas.validar_senha(senha, dados.email, dados.nome)
    except senhas.SenhaFraca as exc:
        sys.exit(str(exc))

    with SessionLocal() as db:
        if db.scalar(select(Usuario).where(Usuario.email == dados.email)):
            sys.exit("Já existe um usuário com este e-mail.")
        db.add(
            Usuario(
                email=dados.email,
                nome=dados.nome,
                senha_hash=gerar_hash_senha(dados.senha),
                papel=PapelUsuario.ADMIN,
            )
        )
        db.commit()
    print(f"Administrador {dados.email} criado.")


def redefinir_senha(email: str, desligar_2fa: bool) -> None:
    with SessionLocal() as db:
        usuario = db.scalar(select(Usuario).where(Usuario.email == email.strip().lower()))
        if usuario is None:
            sys.exit("Nenhum usuário com este e-mail.")
        if usuario.provedor_auth != ProvedorAuth.LOCAL:
            sys.exit("Este usuário entra pelo Clerk: a senha é trocada lá.")
        senha = getpass.getpass("Nova senha (mín. 12 caracteres): ")
        if senha != getpass.getpass("Confirme a nova senha: "):
            sys.exit("As senhas não conferem.")
        if not SENHA_MIN <= len(senha) <= SENHA_MAX:
            sys.exit(f"A senha precisa ter de {SENHA_MIN} a {SENHA_MAX} caracteres.")
        try:
            senhas.validar_senha(senha, usuario.email, usuario.nome)
        except senhas.SenhaFraca as exc:
            sys.exit(str(exc))
        usuario.senha_hash = gerar_hash_senha(senha)
        usuario.trocar_senha = False
        usuario.tentativas_falhas, usuario.bloqueado_ate = 0, None
        if desligar_2fa and usuario.totp_ativo:
            dois_fatores.desativar(db, usuario)
        services.derrubar_sessoes(db, usuario)
        auditoria.registrar(db, "usuario.senha_redefinida_cli", "usuario", usuario.id,
                            detalhes={"email": usuario.email, "2fa_desligado": desligar_2fa})
        db.commit()
        avisos = " A verificação em duas etapas foi desligada." if desligar_2fa else (
            " A verificação em duas etapas continua ativa: tenha o app autenticador à mão." if usuario.totp_ativo else "")
    print(f"Senha de {email} redefinida.{avisos}")


def limpar_tokens() -> None:
    agora = agora_utc()
    with SessionLocal() as db:
        resultado = db.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.expira_em < agora,
                    RefreshToken.revogado_em < agora - RETENCAO_REVOGADOS,
                )
            )
        )
        db.commit()
    print(f"{resultado.rowcount} refresh token(s) removido(s).")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python3 -m app.cli")
    sub = parser.add_subparsers(dest="comando", required=True)
    admin = sub.add_parser("criar-admin", help="Cria um usuário administrador")
    admin.add_argument("--email", required=True)
    admin.add_argument("--nome", required=True)
    sub.add_parser("limpar-tokens", help="Remove refresh tokens vencidos/revogados")
    redefinir = sub.add_parser("redefinir-senha", help="Define uma nova senha (pedida no terminal) para um usuário")
    redefinir.add_argument("--email", required=True)
    redefinir.add_argument("--desligar-2fa", action="store_true", help="Também desliga a verificação em duas etapas")

    args = parser.parse_args()
    if args.comando == "criar-admin":
        criar_admin(args.email, args.nome)
    elif args.comando == "redefinir-senha":
        redefinir_senha(args.email, args.desligar_2fa)
    elif args.comando == "limpar-tokens":
        limpar_tokens()


if __name__ == "__main__":
    main()
