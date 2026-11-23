"""Leitura e gravação dos arquivos JSON (no lugar de um banco de dados).

dados/logins.json
    {"admin": {"usuario": "...", "senha": "<hash>"},
     "pessoas": [{"id": "joao-silva", "nome": "João Silva", "foto": "joao-silva-1732391234567.jpg",
                  "joga": true}, ...]}
    "pessoas" é o cadastro de todo mundo que já jogou; "joga" marca quem está na lista da semana.

dados/avaliacoes.json
    {"avaliacoes": {"<id de quem avaliou>": {"notas": {"<id>": 4, ...}, "nao_conhece": ["<id>", ...],
                                             "data": "..."}, ...}}

dados/linhas.json
    {"linhas": [["<id>", ...], ...], "publicado": false, "data": "...", "por_linha": 5, "equilibrar": true}
    As linhas (times) sorteadas; só aparecem na página inicial depois de publicadas.

As fotos ficam em MEDIA_ROOT/fotos/.
"""

import copy
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
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
LINHAS = "linhas.json"
FUSO = ZoneInfo("America/Maceio")
TAMANHO_MAX_FOTO = 10 * 1024 * 1024

_PADRAO = {
    LOGINS: {"admin": None, "pessoas": []},
    AVALIACOES: {"avaliacoes": {}},
    LINHAS: {"linhas": [], "publicado": False, "data": None, "por_linha": 5, "equilibrar": True},
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
    Não chame editar() dentro de outro editar().
    """
    with _trava_thread, open(_pasta() / ".trava", "w") as trava:
        if fcntl:
            fcntl.flock(trava, fcntl.LOCK_EX)
        dados = ler(arquivo)
        yield dados
        _gravar(arquivo, dados)


def agora():
    return datetime.now(FUSO).isoformat(timespec="seconds")


def ordem_alfabetica(nome):
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return sem_acento.casefold()


# ---------- admin ----------

def admin_configurado():
    return bool(ler(LOGINS).get("admin"))


def conferir_admin(usuario, senha):
    admin = ler(LOGINS).get("admin")
    return bool(admin) and usuario == admin["usuario"] and check_password(senha, admin["senha"])


def definir_admin(usuario, senha):
    with editar(LOGINS) as dados:
        dados["admin"] = {"usuario": usuario, "senha": make_password(senha)}


# ---------- cadastro e lista da semana ----------

def pessoas():
    """Cadastro completo, em ordem alfabética."""
    # quem foi cadastrado antes de existir "joga"/"foto" estava na lista, então joga
    return [{"foto": None, "joga": True, **p} for p in ler(LOGINS)["pessoas"]]


def jogadores():
    """Só quem está na lista da semana."""
    return [p for p in pessoas() if p["joga"]]


def buscar_jogador(pessoa_id):
    return next((p for p in jogadores() if p["id"] == pessoa_id), None)


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


def _novo_id(nome, usados):
    base = slugify(nome) or "pessoa"
    pid, n = base, 2
    while pid in usados:
        pid, n = f"{base}-{n}", n + 1
    return pid


def adicionar_pessoas(nomes, somente_estes=False):
    """Marca os nomes como jogando esta semana, cadastrando quem ainda não existe.

    Com somente_estes=True, quem não está em `nomes` sai da lista da semana.
    Devolve (quantos foram cadastrados agora, quantos já existiam).
    """
    novos = existentes = 0
    with editar(LOGINS) as dados:
        lista = dados["pessoas"]
        por_nome = {p["nome"].casefold(): p for p in lista}
        usados = {p["id"] for p in lista}
        if somente_estes:
            for p in lista:
                p["joga"] = False
        for nome in nomes:
            pessoa = por_nome.get(nome.casefold())
            if pessoa:
                pessoa["joga"] = True
                existentes += 1
            else:
                pessoa = {"id": _novo_id(nome, usados), "nome": nome, "foto": None, "joga": True}
                usados.add(pessoa["id"])
                por_nome[nome.casefold()] = pessoa
                lista.append(pessoa)
                novos += 1
        lista.sort(key=lambda p: ordem_alfabetica(p["nome"]))
    return novos, existentes


def salvar_cadastro(ids_no_formulario, nomes, jogam):
    """Atualiza nome e "joga" de quem estava no formulário. O id não muda ao renomear.

    Devolve a lista de nomes que não puderam ser usados por já existirem.
    """
    repetidos = []
    with editar(LOGINS) as dados:
        lista = dados["pessoas"]
        for p in lista:
            if p["id"] not in ids_no_formulario:
                continue
            p["joga"] = p["id"] in jogam
            novo = " ".join(nomes.get(p["id"], "").split())
            if novo and novo != p["nome"]:
                if any(o is not p and o["nome"].casefold() == novo.casefold() for o in lista):
                    repetidos.append(novo)
                else:
                    p["nome"] = novo
        lista.sort(key=lambda p: ordem_alfabetica(p["nome"]))
    return repetidos


def excluir_pessoa(pessoa_id):
    with editar(LOGINS) as dados:
        pessoa = next((p for p in dados["pessoas"] if p["id"] == pessoa_id), None)
        if pessoa:
            dados["pessoas"].remove(pessoa)
    if not pessoa:
        return None
    # Some também das avaliações, para ninguém novo com o mesmo id herdar as notas.
    with editar(AVALIACOES) as dados:
        dados["avaliacoes"].pop(pessoa_id, None)
        for registro in dados["avaliacoes"].values():
            for campo in ("notas", "ataque", "defesa"):  # ataque/defesa: avaliações da versão antiga
                registro.get(campo, {}).pop(pessoa_id, None)
            for campo in ("nao_conhece", "ranking"):
                if pessoa_id in registro.get(campo, []):
                    registro[campo].remove(pessoa_id)
    with editar(LINHAS) as dados:
        dados["linhas"] = [[j for j in linha if j != pessoa_id] for linha in dados["linhas"]]
    _apagar_foto(pessoa.get("foto"))
    return pessoa


# ---------- fotos ----------

ARQUIVO_FOTO = re.compile(r"^[a-z0-9_-]+-\d+\.(jpg|png|webp|gif)$")


def pasta_fotos():
    pasta = Path(settings.MEDIA_ROOT) / "fotos"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _tipo_imagem(inicio):
    if inicio.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if inicio.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if inicio[:4] == b"RIFF" and inicio[8:12] == b"WEBP":
        return "webp"
    if inicio.startswith(b"GIF8"):
        return "gif"
    return None


def _apagar_foto(arquivo):
    if arquivo and ARQUIVO_FOTO.match(arquivo):
        (pasta_fotos() / arquivo).unlink(missing_ok=True)


def salvar_foto(pessoa_id, arquivo_enviado):
    """Guarda a foto (JPEG, PNG, WebP ou GIF de até 10 MB). Devolve False se não for uma imagem válida."""
    extensao = _tipo_imagem(arquivo_enviado.read(16))
    arquivo_enviado.seek(0)
    if not extensao or arquivo_enviado.size > TAMANHO_MAX_FOTO:
        return False
    # nome novo a cada troca, para o navegador não mostrar a foto antiga do cache
    nome_arquivo = f"{pessoa_id}-{time.time_ns() // 1_000_000}.{extensao}"
    with open(pasta_fotos() / nome_arquivo, "wb") as destino:
        for pedaco in arquivo_enviado.chunks():
            destino.write(pedaco)

    antiga = None
    with editar(LOGINS) as dados:
        pessoa = next((p for p in dados["pessoas"] if p["id"] == pessoa_id), None)
        if pessoa:
            antiga, pessoa["foto"] = pessoa.get("foto"), nome_arquivo
    _apagar_foto(antiga if pessoa else nome_arquivo)
    return bool(pessoa)


def remover_foto(pessoa_id):
    antiga = None
    with editar(LOGINS) as dados:
        for p in dados["pessoas"]:
            if p["id"] == pessoa_id:
                antiga, p["foto"] = p.get("foto"), None
    _apagar_foto(antiga)


# ---------- avaliacoes.json ----------

def avaliacoes():
    return ler(AVALIACOES)["avaliacoes"]


def salvar_avaliacao(avaliador_id, registro):
    with editar(AVALIACOES) as dados:
        dados["avaliacoes"][avaliador_id] = {**registro, "data": agora()}


def zerar_avaliacoes():
    with editar(AVALIACOES) as dados:
        dados["avaliacoes"] = {}


# ---------- linhas.json ----------

def linhas():
    return {**_PADRAO[LINHAS], **ler(LINHAS)}


def salvar_sorteio(novas_linhas, por_linha, equilibrar):
    """Sorteio novo: fica escondido da galera até ser publicado."""
    with editar(LINHAS) as dados:
        dados.update(linhas=novas_linhas, publicado=False, data=agora(), por_linha=por_linha, equilibrar=equilibrar)


def ajustar_linhas(novas_linhas):
    """Ajuste manual: se já estava publicado, continua publicado (e a galera já vê o ajuste)."""
    with editar(LINHAS) as dados:
        dados["linhas"] = [linha for linha in novas_linhas if linha]
        dados["data"] = agora()


def publicar_linhas():
    with editar(LINHAS) as dados:
        dados["publicado"] = True
        dados["data"] = agora()
