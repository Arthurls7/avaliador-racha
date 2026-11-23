"""Leitura e gravação dos arquivos JSON (no lugar de um banco de dados).

dados/logins.json
    {"admin": {"usuario": "...", "senha": "<hash>"},
     "pessoas": [{"id": "joao-silva", "nome": "João Silva"}, ...]}

dados/avaliacoes.json
    {"avaliacoes": {"<id de quem avaliou>": {...registro...}, ...}}
"""

import copy
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils.text import slugify

try:  # trava entre processos (Linux/PythonAnywhere); no Windows usa só a trava de thread
    import fcntl
except ImportError:
    fcntl = None

LOGINS = "logins.json"
AVALIACOES = "avaliacoes.json"
FUSO = ZoneInfo("America/Maceio")

_PADRAO = {
    LOGINS: {"admin": None, "pessoas": []},
    AVALIACOES: {"avaliacoes": {}},
}
_trava_thread = threading.Lock()


def _pasta():
    pasta = Path(getattr(settings, "VOTACAO_DADOS_DIR", settings.BASE_DIR / "dados"))
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def ler(arquivo):
    caminho = _pasta() / arquivo
    if not caminho.exists():
        return copy.deepcopy(_PADRAO[arquivo])
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def _gravar(arquivo, dados):
    # Grava num arquivo temporário e troca de uma vez, para nunca deixar um JSON pela metade.
    pasta = _pasta()
    fd, temporario = tempfile.mkstemp(dir=pasta, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(temporario, pasta / arquivo)


@contextmanager
def editar(arquivo):
    """Lê o arquivo, deixa o bloco alterar os dados e grava no final.

    A trava evita que duas pessoas salvando ao mesmo tempo apaguem a avaliação uma da outra.
    """
    with _trava_thread, open(_pasta() / ".trava", "w") as trava:
        if fcntl:
            fcntl.flock(trava, fcntl.LOCK_EX)
        dados = ler(arquivo)
        yield dados
        _gravar(arquivo, dados)


def agora():
    return datetime.now(FUSO).isoformat(timespec="seconds")


# ---------- logins.json ----------

def pessoas():
    return ler(LOGINS)["pessoas"]


def buscar_pessoa(pessoa_id):
    return next((p for p in pessoas() if p["id"] == pessoa_id), None)


def admin_configurado():
    return bool(ler(LOGINS).get("admin"))


def conferir_admin(usuario, senha):
    admin = ler(LOGINS).get("admin")
    return bool(admin) and usuario == admin["usuario"] and check_password(senha, admin["senha"])


def definir_admin(usuario, senha):
    with editar(LOGINS) as dados:
        dados["admin"] = {"usuario": usuario, "senha": make_password(senha)}


_NUMERACAO = re.compile(r"^\s*\d+\s*[.)\-–:]\s*")  # "1. Fulano", "2 - Ciclano", "3) Beltrano"


def limpar_nomes(texto):
    """Um nome por linha; ignora linhas vazias, numeração no início e nomes repetidos."""
    nomes, vistos = [], set()
    for linha in texto.splitlines():
        nome = " ".join(_NUMERACAO.sub("", linha).split())
        if nome and nome.casefold() not in vistos:
            vistos.add(nome.casefold())
            nomes.append(nome)
    return nomes


def definir_pessoas(nomes):
    """Salva a lista. Quem já existia mantém o mesmo id (e as avaliações)."""
    with editar(LOGINS) as dados:
        ids_existentes = {p["nome"].casefold(): p["id"] for p in dados["pessoas"]}
        ids = {nome: ids_existentes[nome.casefold()] for nome in nomes if nome.casefold() in ids_existentes}
        usados = set(ids.values())
        for nome in nomes:
            if nome not in ids:
                base = slugify(nome) or "pessoa"
                pid, n = base, 2
                while pid in usados:
                    pid, n = f"{base}-{n}", n + 1
                ids[nome] = pid
                usados.add(pid)
        dados["pessoas"] = [{"id": ids[nome], "nome": nome} for nome in nomes]
    return dados["pessoas"]


# ---------- avaliacoes.json ----------

def avaliacoes():
    return ler(AVALIACOES)["avaliacoes"]


def salvar_avaliacao(avaliador_id, registro):
    with editar(AVALIACOES) as dados:
        dados["avaliacoes"][avaliador_id] = {**registro, "data": agora()}


def zerar_avaliacoes():
    with editar(AVALIACOES) as dados:
        dados["avaliacoes"] = {}
