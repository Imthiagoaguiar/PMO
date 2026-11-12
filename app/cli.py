"""
Comandos administrativos.

    python3 -m app.cli criar-admin --email admin@empresa.com.br --nome "Administrador"
    python3 -m app.cli limpar-tokens

`criar-admin` pede a senha no terminal (não fica no histórico do shell).
`limpar-tokens` apaga refresh tokens vencidos ou revogados — agende para rodar 1x por dia (cron).
"""

import argparse
import getpass
import sys
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import delete, or_, select

from app.database import SessionLocal
from app.models import PapelUsuario, RefreshToken, Usuario
from app.schemas import UsuarioCreate
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

    args = parser.parse_args()
    if args.comando == "criar-admin":
        criar_admin(args.email, args.nome)
    elif args.comando == "limpar-tokens":
        limpar_tokens()


if __name__ == "__main__":
    main()
