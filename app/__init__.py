import sys

# As versões corrigidas de várias dependências (ex.: python-multipart, que lê os uploads)
# não existem para Python antigo. Melhor não subir do que subir vulnerável.
if sys.version_info < (3, 11):
    raise RuntimeError(
        f"O PMO exige Python 3.11 ou mais novo (este é {sys.version.split()[0]}). "
        "Use a venv do projeto: source .venv/bin/activate"
    )
