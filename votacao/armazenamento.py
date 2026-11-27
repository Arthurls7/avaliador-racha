"""Leitura e gravação dos arquivos JSON (no lugar de um banco de dados).

dados/logins.json
    {"admin": {"usuario": "...", "senha": "<hash>"},
     "pessoas": [{"id": "joao-silva", "nome": "João Silva", "foto": "joao-silva-1732391234567.jpg",
                  "joga": true, "senha": "0427"}, ...]}
    "pessoas" é o cadastro de todo mundo que já jogou; "joga" marca quem está na lista da semana.
    "senha" é a senha de 4 números de cada jogador. Fica em texto para o organizador poder ver e repassar
    (com só 4 números, criptografar não protegeria nada; quem protege é o limite de tentativas erradas).

dados/avaliacoes.json
    {"avaliacoes": {"<id de quem avaliou>": {"notas": {"<id>": 4, ...}, "nao_conhece": ["<id>", ...],
                                             "data": "..."}, ...}}

dados/linhas.json
    {"linhas": [["<id>", ...], ...], "publicado": false, "data": "...",
     "divisao": "por_linha" | "num_linhas", "por_linha": 5, "num_linhas": 3, "equilibrar": true,
     "diferenca_max": 0.5, "diferenca": 0.12, "juntos": [["<id>", "<id>"], ...], "separados": [[...], ...],
     "pedidos": [{"id": "a1b2c3d4", "jogador": "<id>", "tipo": "troca" | "mudar", "com": "<id>" | null,
                  "destino": 2 | null, "motivo": "...", "por": "<id de quem sugeriu>" | null, "data": "...",
                  "status": "pendente" | "aprovado" | "recusado", "resolvido_em": "..." | null}, ...]}
    As linhas (times) sorteadas; só aparecem na página inicial depois de publicadas.
    "divisao", "por_linha", "num_linhas", "equilibrar" e "diferenca_max" são as escolhas do último sorteio
    (o painel lembra delas); "diferenca" é a diferença entre a linha mais forte e a mais fraca naquele sorteio.
    "juntos" são as duplas que têm que cair na mesma linha nos sorteios e "separados", as que têm que cair em
    linhas diferentes (as duas valem até o organizador tirar).
    "pedidos" é a fila de sugestões de troca de linha (quem está conectado sugere trocar dois jogadores);
    o organizador aprova ou recusa, na ordem em que chegaram.

dados/historico.json
    {"semanas": [{"data": "...", "jogadores": 15, "votaram": 9,
                  "ranking": [{"id": "...", "nome": "...", "nota": 7.4, "qtd": 8}, ...],
                  "sem_nota": ["Nome", ...], "linhas": [["Nome", ...], ...]}, ...]}
    O retrato de cada semana fechada, da mais antiga para a mais nova (com os nomes, para não depender do
    cadastro de hoje).

As fotos ficam em MEDIA_ROOT/fotos/.
"""

import copy
import json
import os
import re
import secrets
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
HISTORICO = "historico.json"
FUSO = ZoneInfo("America/Maceio")
TAMANHO_MAX_FOTO = 10 * 1024 * 1024

_PADRAO = {
    LOGINS: {"admin": None, "pessoas": []},
    AVALIACOES: {"avaliacoes": {}},
    LINHAS: {"linhas": [], "publicado": False, "data": None, "divisao": "por_linha", "por_linha": 5, "num_linhas": 2,
             "equilibrar": True, "diferenca_max": 0.5, "juntos": [], "separados": [], "pedidos": []},
    HISTORICO: {"semanas": []},
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
    # quem foi cadastrado antes de existir "joga"/"foto"/"senha" estava na lista, então joga
    return [{"foto": None, "joga": True, "senha": None, **p} for p in ler(LOGINS)["pessoas"]]


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


def _senha_nova(usadas):
    """4 números aleatórios (pode começar com 0), sem repetir a de outro jogador."""
    while True:
        senha = f"{secrets.randbelow(10000):04d}"
        if senha not in usadas or len(usadas) >= 10000:
            return senha


def garantir_senhas():
    """Cria senha para quem ainda não tem (ex.: cadastrados antes de existir senha). Devolve quantas criou."""
    if all(p.get("senha") for p in ler(LOGINS)["pessoas"]):
        return 0
    criadas = 0
    with editar(LOGINS) as dados:
        usadas = {p.get("senha") for p in dados["pessoas"]}
        for p in dados["pessoas"]:
            if not p.get("senha"):
                p["senha"] = _senha_nova(usadas)
                usadas.add(p["senha"])
                criadas += 1
    return criadas


def nova_senha(pessoa_id):
    """Troca a senha de alguém (a antiga para de funcionar). Devolve a pessoa, ou None."""
    with editar(LOGINS) as dados:
        pessoa = next((p for p in dados["pessoas"] if p["id"] == pessoa_id), None)
        if pessoa:
            pessoa["senha"] = _senha_nova({p.get("senha") for p in dados["pessoas"]})
    return pessoa


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
        senhas = {p.get("senha") for p in lista}
        if somente_estes:
            for p in lista:
                p["joga"] = False
        for nome in nomes:
            pessoa = por_nome.get(nome.casefold())
            if pessoa:
                pessoa["joga"] = True
                existentes += 1
            else:
                pessoa = {"id": _novo_id(nome, usados), "nome": nome, "foto": None, "joga": True,
                          "senha": _senha_nova(senhas)}
                usados.add(pessoa["id"])
                senhas.add(pessoa["senha"])
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
        dados["pedidos"] = [p for p in dados.get("pedidos", []) if pessoa_id not in (p["jogador"], p.get("com"))]
        for regra in REGRAS:
            dados[regra] = [dupla for dupla in dados.get(regra, []) if pessoa_id not in dupla]
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


def salvar_sorteio(novas_linhas, por_linha, equilibrar, **escolhas):
    """Sorteio novo: fica escondido da galera até ser publicado. Os pedidos de troca antigos são apagados.

    `escolhas` guarda o resto do que o organizador escolheu (divisao, num_linhas, diferenca_max, diferenca).
    """
    with editar(LINHAS) as dados:
        dados.update(linhas=novas_linhas, publicado=False, data=agora(), por_linha=por_linha, equilibrar=equilibrar,
                     pedidos=[], **escolhas)


def ajustar_linhas(novas_linhas):
    """Ajuste manual: se já estava publicado, continua publicado (e a galera já vê o ajuste)."""
    with editar(LINHAS) as dados:
        dados["linhas"] = [linha for linha in novas_linhas if linha]
        dados["data"] = agora()


def publicar_linhas():
    with editar(LINHAS) as dados:
        dados["publicado"] = True
        dados["data"] = agora()


def despublicar_linhas():
    """Esconde as linhas da página inicial (o sorteio continua salvo para ajustar e publicar de novo)."""
    with editar(LINHAS) as dados:
        dados["publicado"] = False


REGRAS = ("juntos", "separados")  # quem cai junto / quem não cai junto


def adicionar_regra(regra, a, b):
    """Guarda a dupla em "juntos" ou "separados". Devolve "ok", "repetida" ou "contraria" (já está na outra)."""
    outra = REGRAS[1 - REGRAS.index(regra)]
    with editar(LINHAS) as dados:
        if any(set(dupla) == {a, b} for dupla in dados.get(regra, [])):
            return "repetida"
        if any(set(dupla) == {a, b} for dupla in dados.get(outra, [])):
            return "contraria"
        dados.setdefault(regra, []).append([a, b])
    return "ok"


def remover_regra(regra, a, b):
    with editar(LINHAS) as dados:
        dados[regra] = [dupla for dupla in dados.get(regra, []) if set(dupla) != {a, b}]


def apagar_linhas():
    """Apaga o sorteio (e a fila de trocas). As escolhas do sorteio e as regras continuam."""
    with editar(LINHAS) as dados:
        dados.update(linhas=[], publicado=False, data=None, pedidos=[])


# ---------- historico.json ----------

def historico():
    return ler(HISTORICO)["semanas"]


def guardar_semana(retrato):
    with editar(HISTORICO) as dados:
        dados["semanas"].append({**retrato, "data": agora()})


def limpar_historico():
    with editar(HISTORICO) as dados:
        dados["semanas"] = []


def tirar_todos_da_semana():
    with editar(LOGINS) as dados:
        for pessoa in dados["pessoas"]:
            pessoa["joga"] = False


# ---------- pedidos de troca de linha (também em linhas.json) ----------

MAX_PEDIDOS_RESOLVIDOS = 30  # guarda só os últimos, para o arquivo não crescer para sempre
MAX_PEDIDOS_NA_FILA = 50     # um teto para ninguém encher a fila


def pedidos():
    return linhas()["pedidos"]


def _mesma_sugestao(a, b):
    """Mesma troca (em qualquer ordem) ou mesma pessoa indo para a mesma linha."""
    if a["tipo"] != b["tipo"]:
        return False
    if a["tipo"] == "troca":
        return {a["jogador"], a.get("com")} == {b["jogador"], b.get("com")}
    return a["jogador"] == b["jogador"] and a.get("destino") == b.get("destino")


def pedir_troca(jogador, tipo, com=None, destino=None, motivo="", por=None):
    """Põe a sugestão no fim da fila (nenhuma substitui outra).

    Devolve "ok", "repetido" (a mesma sugestão já está na fila) ou "cheia".
    `por` é quem sugeriu (o jogador conectado; pode ser None em sugestões antigas).
    """
    novo = {
        "id": secrets.token_hex(4), "jogador": jogador, "tipo": tipo, "com": com, "destino": destino,
        "motivo": motivo, "por": por, "data": agora(), "status": "pendente", "resolvido_em": None,
    }
    with editar(LINHAS) as dados:
        lista = dados.setdefault("pedidos", [])
        pendentes = [p for p in lista if p["status"] == "pendente"]
        if any(_mesma_sugestao(p, novo) for p in pendentes):
            return "repetido"
        if len(pendentes) >= MAX_PEDIDOS_NA_FILA:
            return "cheia"
        lista.append(novo)
    return "ok"


def resolver_pedido(pedido_id, aprovado, novas_linhas=None):
    """Marca o pedido como aprovado (gravando as linhas com a troca) ou recusado. Devolve o pedido, ou None."""
    with editar(LINHAS) as dados:
        lista = dados.get("pedidos", [])
        pedido = next((p for p in lista if p["id"] == pedido_id and p["status"] == "pendente"), None)
        if pedido:
            pedido["status"] = "aprovado" if aprovado else "recusado"
            pedido["resolvido_em"] = agora()
            if aprovado:
                dados["linhas"] = [linha for linha in novas_linhas if linha]
                dados["data"] = agora()
            pendentes = [p for p in lista if p["status"] == "pendente"]
            resolvidos = [p for p in lista if p["status"] != "pendente"][-MAX_PEDIDOS_RESOLVIDOS:]
            dados["pedidos"] = resolvidos + pendentes
    return pedido
