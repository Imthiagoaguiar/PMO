"""
Teste de carga do PMO: muitos usuários e muitos projetos usando o sistema ao mesmo tempo.

Roda SEMPRE num banco descartável cujo nome contém "carga" (recusa qualquer outro). Use pelo
scripts/carga.sh, que cria o banco, sobe a API e o worker numa porta própria e apaga tudo no fim.

    python scripts/carga.py semear  --usuarios 400 --projetos 2000 --tarefas 25
    python scripts/carga.py rodar   --url http://localhost:8200 --fases 50:60,150:60,300:60
    python scripts/carga.py login-mesmo-ip --url http://localhost:8200 --quantos 120

O que cada usuário virtual faz (com pausas de 0,2 a 1 s, como uma pessoa clicando):
listar projetos, abrir um projeto, indicadores, pilares, orçamento, minhas tarefas, histórico,
atualizar o % de uma tarefa, comentar, lançar um gasto, gerar PDF, exportar Excel e importar
uma planilha pequena (gerentes). Cada um "vem" de um IP diferente (X-Forwarded-For, como o Caddy faz).
"""

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

SENHA = "Carga-Ponte-Azul-2919"


# ---------- Semeadura ----------


def semear(usuarios: int, projetos: int, tarefas_por_projeto: int) -> None:
    url = os.environ.get("DATABASE_URL", "")
    if "carga" not in url.rsplit("/", 1)[-1]:
        sys.exit("carga.py semear só roda num banco cujo nome contém 'carga' (nunca no banco de verdade)")

    from sqlalchemy import insert, select

    from app.database import SessionLocal
    from app.models import (
        FASES_WATERFALL, PILARES_PADRAO, DependenciaTarefa, Fase, ItemOrcamento, LancamentoOrcamento,
        MarcoLinhaTempo, PapelProjeto, PapelUsuario, Pilar, Projeto, ProjetoMembro, Tarefa, Usuario,
        chave_da_fase, chave_do_pilar,
    )
    from app.security import gerar_hash_senha

    aleatorio = random.Random(42)
    inicio = time.perf_counter()
    hash_ = gerar_hash_senha(SENHA)  # um Argon2 só: o mesmo hash para todos (senha igual)
    gerentes = max(1, usuarios // 4)
    with SessionLocal() as db:
        db.execute(insert(Usuario), [
            {"email": f"u{i}@carga.com", "nome": f"Usuário {i}", "senha_hash": hash_, "ativo": True,
             "papel": (PapelUsuario.GERENTE if i < gerentes else PapelUsuario.MEMBRO), "provedor_auth": "local",
             "tentativas_falhas": 0, "trocar_senha": False, "versao_sessao": 0, "totp_ativo": False}
            for i in range(usuarios)
        ])
        ids_usuarios = list(db.scalars(select(Usuario.id).order_by(Usuario.id)))
        ids_gerentes = ids_usuarios[:gerentes]

        db.execute(insert(Projeto), [
            {"nome_projeto": f"Projeto de carga {i}", "status": "Em andamento", "dono_id": ids_gerentes[i % gerentes],
             "data_inicio": date(2026, 1, 5), "orcamento_aprovado": Decimal("500000.00"), "versao": 1,
             "seguir_ordem_fases": True}
            for i in range(projetos)
        ])
        ids_projetos = list(db.scalars(select(Projeto.id).order_by(Projeto.id)))
        donos = dict(db.execute(select(Projeto.id, Projeto.dono_id)).all())

        db.execute(insert(Fase), [
            {"projeto_id": p, "titulo": t, "titulo_chave": chave_da_fase(t), "ordem_sequencia": o}
            for p in ids_projetos for o, t in enumerate(FASES_WATERFALL, start=1)
        ])
        db.execute(insert(Pilar), [
            {"projeto_id": p, "nome": n, "nome_chave": chave_do_pilar(n), "ordem": o, "versao": 1}
            for p in ids_projetos for o, n in enumerate(PILARES_PADRAO)
        ])
        membros = []
        for p in ids_projetos:
            outros = aleatorio.sample(ids_usuarios, 4)
            vistos = {donos[p]}
            membros.append({"projeto_id": p, "usuario_id": donos[p], "papel": PapelProjeto.EDITOR})
            for u in outros:
                if u not in vistos:
                    vistos.add(u)
                    papel = PapelProjeto.EDITOR if aleatorio.random() < 0.6 else PapelProjeto.VISUALIZADOR
                    membros.append({"projeto_id": p, "usuario_id": u, "papel": papel})
        db.execute(insert(ProjetoMembro), membros)
        db.commit()

        fases = defaultdict(list)
        for fid, pid in db.execute(select(Fase.id, Fase.projeto_id).order_by(Fase.ordem_sequencia)):
            fases[pid].append(fid)
        pilares = defaultdict(list)
        for pid_, prj in db.execute(select(Pilar.id, Pilar.projeto_id)):
            pilares[prj].append(pid_)

        linhas = []
        for p in ids_projetos:
            for i in range(tarefas_por_projeto):
                ini = date(2026, 1, 5) + timedelta(days=aleatorio.randint(0, 300))
                linhas.append({
                    "fase_id": fases[p][min(i * 5 // tarefas_por_projeto, 4)], "descricao": f"Tarefa {i} do projeto {p}",
                    "data_inicio": ini, "data_fim": ini + timedelta(days=aleatorio.randint(1, 40)),
                    "percentual_conclusao": aleatorio.choice([0, 0, 25, 50, 100]), "pilar_id": aleatorio.choice(pilares[p]),
                    "responsavel_id": donos[p], "versao": 1,
                })
        for lote in range(0, len(linhas), 5000):
            db.execute(insert(Tarefa), linhas[lote: lote + 5000])
        db.commit()

        tarefas = defaultdict(list)
        for tid, pid in db.execute(select(Tarefa.id, Fase.projeto_id).join(Fase)):
            tarefas[pid].append(tid)
        deps, marcos, itens = [], [], []
        for p in ids_projetos:
            t = sorted(tarefas[p])
            for a, b in zip(t[0:10], t[1:11]):  # uma cadeia de 10 tarefas, "fim após fim" (nunca nasce quebrada)
                deps.append({"projeto_id": p, "predecessora_id": a, "sucessora_id": b, "tipo": "fim_apos_fim"})
            marcos += [{"projeto_id": p, "titulo": "Pré-entrega", "data": date(2026, 6, 15), "forma": "bandeira",
                        "cor": "vermelho", "versao": 1},
                       {"projeto_id": p, "titulo": "Congelamento", "data": date(2026, 11, 2), "data_fim": date(2026, 11, 20),
                        "forma": "faixa", "cor": "cinza", "versao": 1}]
            itens += [{"projeto_id": p, "descricao": nome, "categoria": cat, "pilar_id": pilares[p][k],
                       "valor_orcado": Decimal(valor), "ordem": k, "versao": 1}
                      for k, (nome, cat, valor) in enumerate([("Licenças", "capex", "150000.00"),
                                                              ("Consultoria", "opex", "200000.00"),
                                                              ("Treinamento", "opex", "50000.00")])]
        db.execute(insert(DependenciaTarefa), deps)
        db.execute(insert(MarcoLinhaTempo), marcos)
        db.execute(insert(ItemOrcamento), itens)
        db.commit()
        lancs = [{"projeto_id": p, "item_id": i, "data": date(2026, 1, 5) + timedelta(days=aleatorio.randint(0, 300)),
                  "descricao": "NF", "valor": Decimal(aleatorio.randint(1000, 30000)), "criado_por_id": None}
                 for i, p in db.execute(select(ItemOrcamento.id, ItemOrcamento.projeto_id)) for _ in range(5)]
        for lote in range(0, len(lancs), 5000):
            db.execute(insert(LancamentoOrcamento), lancs[lote: lote + 5000])
        db.commit()
    print(f"Semeado em {time.perf_counter() - inicio:.1f}s: {usuarios} usuários ({gerentes} gerentes), "
          f"{projetos} projetos, {len(linhas)} tarefas, {len(deps)} dependências, {len(itens)} itens, "
          f"{len(lancs)} lançamentos, {len(marcos)} marcos")


# ---------- Medição ----------


def _quantil(ordenados: List[float], fracao: float) -> float:
    return ordenados[min(len(ordenados) - 1, int(fracao * len(ordenados)))]


class Medidor:
    def __init__(self) -> None:
        self.tempos: Dict[str, List[float]] = defaultdict(list)
        self.status: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.exemplos_de_erro: Dict[Tuple[str, int], str] = {}

    def registrar(self, nome: str, segundos: float, status: int, corpo: str = "") -> None:
        self.tempos[nome].append(segundos * 1000)
        self.status[nome][status] += 1
        if (status >= 400 or status == 0) and (nome, status) not in self.exemplos_de_erro:
            self.exemplos_de_erro[(nome, status)] = corpo[:160]

    def relatorio(self, titulo: str, duracao: float) -> str:
        linhas = [f"\n=== {titulo} ===", f"{'rota':<26}{'n':>6}{'p50':>8}{'p95':>8}{'p99':>8}{'máx':>8}  status"]
        total = 0
        for nome in sorted(self.tempos):
            t = sorted(self.tempos[nome])
            total += len(t)
            st = " ".join(f"{s}:{n}" for s, n in sorted(self.status[nome].items()))
            linhas.append(f"{nome:<26}{len(t):>6}{statistics.median(t):>8.0f}{_quantil(t, .95):>8.0f}{_quantil(t, .99):>8.0f}{t[-1]:>8.0f}  {st}")
        erros = sum(n for st in self.status.values() for s, n in st.items() if s >= 500 or s == 0)
        linhas.append(f"total {total} requisições em {duracao:.0f}s = {total / duracao:.1f} req/s; erros 5xx/rede: {erros}")
        for (nome, status), corpo in sorted(self.exemplos_de_erro.items()):
            linhas.append(f"  exemplo {status} em {nome}: {corpo}")
        return "\n".join(linhas)


# ---------- Usuários virtuais ----------


CSV_PEQUENO = ("Fase;Descrição;% Conclusão\n" + "".join(f"Execução;Tarefa importada {i};{i % 101}\n" for i in range(200))).encode()


class UsuarioVirtual:
    def __init__(self, cliente: Any, medidor: Medidor, indice: int, total_usuarios: int, mesmo_ip: bool) -> None:
        self.c, self.m = cliente, medidor
        self.email = f"u{indice % total_usuarios}@carga.com"
        self.gerente = indice % total_usuarios < max(1, total_usuarios // 4)
        ip = "203.0.113.10" if mesmo_ip else f"10.{indice // 65536 % 256}.{indice // 256 % 256}.{indice % 256}"
        self.cabecalhos: Dict[str, str] = {"X-Forwarded-For": ip}
        self.projetos: List[int] = []
        self.detalhe: Dict[int, Any] = {}

    async def pedir(self, nome: str, metodo: str, caminho: str, **kw: Any) -> Optional[Any]:
        inicio = time.perf_counter()
        try:
            r = await self.c.request(metodo, caminho, headers=self.cabecalhos, **kw)
        except Exception as exc:  # timeout, conexão recusada...
            self.m.registrar(nome, time.perf_counter() - inicio, 0, f"{type(exc).__name__}: {exc}")
            return None
        self.m.registrar(nome, time.perf_counter() - inicio, r.status_code, r.text if r.status_code >= 400 else "")
        return r

    async def entrar(self) -> bool:
        r = await self.pedir("POST /auth/login", "POST", "/auth/login", data={"username": self.email, "password": SENHA})
        if r is None or r.status_code != 200:
            return False
        self.cabecalhos["Authorization"] = f"Bearer {r.json()['access_token']}"
        r = await self.pedir("GET /projetos", "GET", "/projetos?limite=50")
        if r is not None and r.status_code == 200:
            self.projetos = [p["id"] for p in r.json()["itens"]]
        return True

    async def uma_acao(self) -> None:
        if not self.projetos:
            await self.pedir("GET /projetos", "GET", "/projetos?limite=20")
            return
        pid = random.choice(self.projetos)
        sorteio = random.random()
        if sorteio < 0.18:
            await self.pedir("GET /projetos", "GET", "/projetos?limite=20")
        elif sorteio < 0.36:
            r = await self.pedir("GET /projetos/{id}", "GET", f"/projetos/{pid}")
            if r is not None and r.status_code == 200:
                self.detalhe[pid] = r.json()
            await self.pedir("GET indicadores", "GET", f"/projetos/{pid}/indicadores")
        elif sorteio < 0.44:
            await self.pedir("GET pilares", "GET", f"/projetos/{pid}/pilares")
        elif sorteio < 0.52:
            await self.pedir("GET orcamento", "GET", f"/projetos/{pid}/orcamento")
        elif sorteio < 0.57:
            await self.pedir("GET minhas tarefas", "GET", "/tarefas/minhas?limite=20")
        elif sorteio < 0.61:
            await self.pedir("GET historico", "GET", f"/projetos/{pid}/historico?limite=20")
        elif sorteio < 0.75:
            proj = self.detalhe.get(pid)
            tarefas = [t for f in (proj or {}).get("fases", []) for t in f["tarefas"]]
            if proj and proj.get("permissoes", {}).get("editar") and tarefas:
                t = random.choice(tarefas)
                await self.pedir("PATCH tarefa", "PATCH", f"/projetos/{pid}/tarefas/{t['id']}",
                                 json={"percentual_conclusao": random.choice([0, 25, 50, 75, 100]), "versao": t["versao"]})
            else:
                await self.pedir("GET /projetos/{id}", "GET", f"/projetos/{pid}")
        elif sorteio < 0.83:
            await self.pedir("POST comentario", "POST", f"/projetos/{pid}/comentarios", json={"texto": "Atualização da carga"})
        elif sorteio < 0.88:
            proj = self.detalhe.get(pid)
            if proj and proj.get("permissoes", {}).get("editar"):
                r = await self.pedir("GET orcamento", "GET", f"/projetos/{pid}/orcamento")
                if r is not None and r.status_code == 200 and r.json()["itens"]:
                    item = random.choice(r.json()["itens"])
                    await self.pedir("POST lancamento", "POST", f"/projetos/{pid}/orcamento/lancamentos", json={
                        "item_id": item["id"], "valor": f"{random.randint(100, 5000)}.00",
                        "data": "2026-11-16", "descricao": "Gasto da carga"})
        elif sorteio < 0.92:
            await self.pedir("GET relatorio (PDF)", "GET", f"/projetos/{pid}/relatorio")
        elif sorteio < 0.96:
            await self.pedir("GET exportar (xlsx)", "GET", f"/projetos/{pid}/exportar")
        elif self.gerente:
            r = await self.pedir("POST importar (fila)", "POST", "/projetos/importar",
                                 data={"nome_projeto": "Importado na carga"}, files={"arquivo": ("p.csv", CSV_PEQUENO, "text/csv")})
            if r is not None and r.status_code == 202:
                imp = r.json()["id"]
                for _ in range(40):  # acompanha a fila como a tela faz
                    await asyncio.sleep(0.5)
                    s = await self.pedir("GET importacao (status)", "GET", f"/importacoes/{imp}")
                    if s is None or s.status_code != 200 or s.json()["status"] in ("concluida", "falhou"):
                        if s is not None and s.status_code == 200 and s.json()["status"] == "falhou":
                            self.m.registrar("importacao falhou", 0, 599, s.text)
                        break
        else:
            await self.pedir("GET /projetos", "GET", "/projetos?limite=20")

    async def viver(self, ate: float) -> None:
        await asyncio.sleep(random.random() * 2)  # não chegam todos no mesmo milissegundo
        if not await self.entrar():
            return
        while time.perf_counter() < ate:
            await self.uma_acao()
            await asyncio.sleep(random.uniform(0.2, 1.0))


async def rodar(url: str, fases: List[Tuple[int, int]], total_usuarios: int, mesmo_ip: bool) -> None:
    import httpx

    for quantos, segundos in fases:
        medidor = Medidor()
        limites = httpx.Limits(max_connections=quantos + 10, max_keepalive_connections=quantos + 10)
        async with httpx.AsyncClient(base_url=url, timeout=60, limits=limites) as cliente:
            inicio = time.perf_counter()
            ate = inicio + segundos
            await asyncio.gather(*(UsuarioVirtual(cliente, medidor, i, total_usuarios, mesmo_ip).viver(ate)
                                   for i in range(quantos)))
            print(medidor.relatorio(f"{quantos} usuários simultâneos por {segundos}s", time.perf_counter() - inicio), flush=True)


async def login_mesmo_ip(url: str, quantos: int, total_usuarios: int) -> None:
    """Um escritório inteiro atrás do mesmo IP entrando às 9h."""
    import httpx

    medidor = Medidor()
    async with httpx.AsyncClient(base_url=url, timeout=60) as cliente:
        inicio = time.perf_counter()
        await asyncio.gather(*(UsuarioVirtual(cliente, medidor, i, total_usuarios, True).entrar() for i in range(quantos)))
        print(medidor.relatorio(f"{quantos} logins ao mesmo tempo, todos do mesmo IP", time.perf_counter() - inicio))


def main() -> None:
    a = argparse.ArgumentParser(description="Teste de carga do PMO (banco descartável)")
    sub = a.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("semear")
    s.add_argument("--usuarios", type=int, default=400)
    s.add_argument("--projetos", type=int, default=2000)
    s.add_argument("--tarefas", type=int, default=25)
    r = sub.add_parser("rodar")
    r.add_argument("--url", default="http://localhost:8200")
    r.add_argument("--fases", default="50:60,150:60,300:60", help="usuários:segundos, separados por vírgula")
    r.add_argument("--usuarios", type=int, default=400)
    r.add_argument("--mesmo-ip", action="store_true")
    m = sub.add_parser("login-mesmo-ip")
    m.add_argument("--url", default="http://localhost:8200")
    m.add_argument("--quantos", type=int, default=120)
    m.add_argument("--usuarios", type=int, default=400)
    args = a.parse_args()
    if args.cmd == "semear":
        semear(args.usuarios, args.projetos, args.tarefas)
    elif args.cmd == "rodar":
        fases = [tuple(int(x) for x in f.split(":")) for f in args.fases.split(",")]
        asyncio.run(rodar(args.url, fases, args.usuarios, args.mesmo_ip))
    else:
        asyncio.run(login_mesmo_ip(args.url, args.quantos, args.usuarios))


if __name__ == "__main__":
    main()
