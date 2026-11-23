import zlib
from datetime import datetime
from functools import wraps

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import armazenamento as arm
from .notas import NAO_CONHECO, OPCOES_NOTA, formatar, formatar_curto, ler_nota, resultados
from .sorteio import forcas, sortear

# cores do círculo com as iniciais de quem ainda não tem foto (todas com bom contraste para texto branco)
CORES = ["#d1495b", "#00798c", "#30638e", "#6a4c93", "#2b9348", "#c8553d", "#3d5a80", "#b5179e", "#7c6a0a"]
# uma cor para cada linha, como coletes
CORES_LINHAS = ["#2b9254", "#e07a1f", "#2f6fbf", "#c0392b", "#7b4fb3", "#1d9aa5", "#b8860b", "#d63384"]
MEDALHAS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _visual(pessoa):
    """Acrescenta o que o template precisa para desenhar o avatar."""
    palavras = pessoa["nome"].split()
    iniciais = palavras[0][0] + (palavras[-1][0] if len(palavras) > 1 else "")
    return {
        **pessoa,
        "iniciais": iniciais.upper(),
        "cor": CORES[zlib.crc32(pessoa["id"].encode()) % len(CORES)],
        "foto_url": reverse("votacao:foto", args=[pessoa["foto"]]) if pessoa.get("foto") else "",
    }


def _jogador_ou_volta(request, pessoa_id):
    pessoa = arm.buscar_jogador(pessoa_id)
    if not pessoa:
        messages.error(request, "Essa pessoa não está na lista desta semana.")
    return _visual(pessoa) if pessoa else None


def _outros(pessoa_id):
    return [_visual(p) for p in arm.jogadores() if p["id"] != pessoa_id]


def _data(registro):
    if not registro or not registro.get("data"):
        return ""
    return datetime.fromisoformat(registro["data"]).strftime("%d/%m às %H:%M")


def _por_nome(pessoas):
    return sorted(pessoas, key=lambda p: arm.ordem_alfabetica(p["nome"]))


def _montar_linhas(ids_por_linha, jogadores):
    """Junta as linhas salvas com quem joga esta semana.

    Quem saiu da lista some das linhas; quem entrou depois do sorteio fica em "fora".
    Devolve (linhas não vazias, fora), com os jogadores (dicts) em ordem alfabética.
    """
    por_id = {p["id"]: p for p in jogadores}
    linhas = [_por_nome([por_id[j] for j in ids if j in por_id]) for ids in ids_por_linha]
    linhas = [linha for linha in linhas if linha]
    nas_linhas = {p["id"] for linha in linhas for p in linha}
    fora = _por_nome([p for p in jogadores if p["id"] not in nas_linhas])
    return linhas, fora


def _medias_da_semana(jogadores):
    return {linha["id"]: linha["nota"] for linha in resultados(jogadores, arm.avaliacoes())}


def foto(request, arquivo):
    if not arm.ARQUIVO_FOTO.match(arquivo):
        raise Http404
    caminho = arm.pasta_fotos() / arquivo
    if not caminho.is_file():
        raise Http404
    resposta = FileResponse(open(caminho, "rb"))
    resposta["Cache-Control"] = "public, max-age=31536000, immutable"  # o nome muda quando a foto muda
    return resposta


# ---------- quem vai avaliar ----------

def inicio(request):
    avaliacoes = arm.avaliacoes()
    pessoas = [{**_visual(p), "avaliou": p["id"] in avaliacoes} for p in arm.jogadores()]
    return render(request, "votacao/inicio.html", {"pessoas": pessoas})


def ver_linhas(request):
    dados = arm.linhas()
    linhas = []
    if dados["publicado"]:
        linhas, _ = _montar_linhas(dados["linhas"], [_visual(p) for p in arm.jogadores()])
    return render(request, "votacao/linhas.html", {
        "linhas": [{"numero": i + 1, "cor": CORES_LINHAS[i % len(CORES_LINHAS)], "jogadores": linha}
                   for i, linha in enumerate(linhas)],
        "data": _data(dados),
    })


def ver_ranking(request):
    """Só a ordem: a média de cada um não aparece."""
    jogadores = [_visual(p) for p in arm.jogadores()]
    avaliacoes = arm.avaliacoes()
    ranking, sem_nota, anterior = [], [], None
    for linha in resultados(jogadores, avaliacoes):
        if linha["nota"] is None:
            sem_nota.append(linha["pessoa"])
            continue
        # empate na média = mesma posição
        empate = anterior is not None and abs(linha["nota"] - anterior) < 1e-9
        posicao = ranking[-1]["posicao"] if empate else len(ranking) + 1
        anterior = linha["nota"]
        ranking.append({"posicao": posicao, "medalha": MEDALHAS.get(posicao, ""), "pessoa": linha["pessoa"]})
    return render(request, "votacao/ranking.html", {
        "ranking": ranking,
        "sem_nota": sem_nota,
        "total": len(jogadores),
        "ja_avaliaram": sum(1 for p in jogadores if p["id"] in avaliacoes),
    })


def avaliar(request, pessoa_id):
    pessoa = _jogador_ou_volta(request, pessoa_id)
    if not pessoa:
        return redirect("votacao:inicio")
    outros = _outros(pessoa_id)
    anterior = arm.avaliacoes().get(pessoa_id) or {}
    notas_antes = anterior.get("notas", {})
    nao_conhecia = set(anterior.get("nao_conhece", []))

    # o que vem marcado: a escolha anterior (avaliações antigas podem ter notas quebradas, que não viram botão)
    escolhas = {pid: int(v) for pid, v in notas_antes.items() if v in OPCOES_NOTA}
    escolhas.update(dict.fromkeys(nao_conhecia, NAO_CONHECO))
    erro = None

    if request.method == "POST" and outros:
        escolhas, faltando = {}, []
        for p in outros:
            bruto = request.POST.get(f"nota_{p['id']}")
            valor = NAO_CONHECO if bruto == NAO_CONHECO else ler_nota(bruto)
            if valor is None:
                faltando.append(p["nome"])
            else:
                escolhas[p["id"]] = valor
        if not faltando:
            arm.salvar_avaliacao(pessoa_id, {
                "notas": {pid: v for pid, v in escolhas.items() if v != NAO_CONHECO},
                "nao_conhece": [pid for pid, v in escolhas.items() if v == NAO_CONHECO],
            })
            messages.success(request, f"Valeu, {pessoa['nome']}! Suas notas foram salvas. 🎉")
            return redirect("votacao:inicio")
        erro = "Faltou escolher uma nota (ou \"Não conheço\") para: " + ", ".join(faltando) + "."

    def nota_anterior(pid):
        if pid in notas_antes:
            return formatar_curto(notas_antes[pid])
        return "não conheço" if pid in nao_conhecia else ""

    return render(request, "votacao/avaliar.html", {
        "pessoa": pessoa,
        "linhas": [{"pessoa": p, "escolha": escolhas.get(p["id"]), "antes": nota_anterior(p["id"])} for p in outros],
        "opcoes": OPCOES_NOTA,
        "nao_conheco": NAO_CONHECO,
        "anterior_data": _data(anterior),
        "erro": erro,
    })


# ---------- administrador ----------

def _exige_admin(view):
    @wraps(view)
    def interna(request, *args, **kwargs):
        if not request.session.get("admin"):
            return redirect("votacao:painel_entrar")
        return view(request, *args, **kwargs)
    return interna


def painel_entrar(request):
    if request.session.get("admin"):
        return redirect("votacao:painel")
    erro = None
    if request.method == "POST":
        if arm.conferir_admin(request.POST.get("usuario", "").strip(), request.POST.get("senha", "")):
            request.session.cycle_key()
            request.session["admin"] = True
            return redirect("votacao:painel")
        erro = "Usuário ou senha incorretos."
    return render(request, "votacao/painel_entrar.html", {
        "configurado": arm.admin_configurado(),
        "erro": erro,
    })


@require_POST
def painel_sair(request):
    request.session.flush()
    return redirect("votacao:inicio")


@_exige_admin
def painel(request):
    linhas = resultados([_visual(p) for p in arm.jogadores()], arm.avaliacoes())
    for linha in linhas:
        linha["nota_fmt"] = formatar(linha["nota"])
        linha["avaliou_data"] = _data(linha["avaliou"])
    return render(request, "votacao/painel.html", {
        "linhas": linhas,
        "ja_avaliaram": sum(1 for linha in linhas if linha["avaliou"]),
    })


@_exige_admin
def painel_cadastro(request):
    return render(request, "votacao/painel_cadastro.html", {
        "cadastro": [_visual(p) for p in arm.pessoas()],
        "total_avaliacoes": len(arm.avaliacoes()),
    })


@_exige_admin
@require_POST
def painel_adicionar(request):
    nomes = arm.limpar_nomes(request.POST.get("nomes", ""))
    if not nomes:
        messages.error(request, "Escreva pelo menos um nome.")
        return redirect("votacao:painel")
    novos, existentes = arm.adicionar_pessoas(nomes, somente_estes=bool(request.POST.get("somente_estes")))
    partes = []
    if novos:
        partes.append(f"{novos} cadastrado(s) agora")
    if existentes:
        partes.append(f"{existentes} já estava(m) no cadastro")
    messages.success(request, "Lista da semana atualizada: " + " e ".join(partes) + ".")
    return redirect("votacao:painel")


@_exige_admin
@require_POST
def painel_cadastro_salvar(request):
    ids = set(request.POST.getlist("ids"))
    nomes = {pid: request.POST.get(f"nome_{pid}", "") for pid in ids}
    repetidos = arm.salvar_cadastro(ids, nomes, set(request.POST.getlist("joga")))
    for nome in repetidos:
        messages.error(request, f"Já existe alguém chamado {nome}; esse nome não foi trocado.")

    fotos_ruins = []
    for pid in ids:
        arquivo = request.FILES.get(f"foto_{pid}")
        if arquivo and not arm.salvar_foto(pid, arquivo):
            fotos_ruins.append(arquivo.name)
    if fotos_ruins:
        messages.error(request, "Não deu para usar estas fotos (precisa ser JPG, PNG ou WebP de até 10 MB): "
                       + ", ".join(fotos_ruins))
    messages.success(request, "Cadastro salvo.")
    return redirect("votacao:painel_cadastro")


@_exige_admin
@require_POST
def painel_remover_foto(request):
    arm.remover_foto(request.POST.get("id", ""))
    messages.success(request, "Foto removida.")
    return redirect("votacao:painel_cadastro")


@_exige_admin
@require_POST
def painel_excluir(request):
    pessoa = arm.excluir_pessoa(request.POST.get("id", ""))
    if pessoa:
        messages.success(request, f"{pessoa['nome']} foi excluído(a) do cadastro.")
    return redirect("votacao:painel_cadastro")


@_exige_admin
@require_POST
def painel_zerar(request):
    arm.zerar_avaliacoes()
    messages.success(request, "Todas as avaliações foram apagadas.")
    return redirect("votacao:painel_cadastro")


# ---------- administrador: linhas ----------

@_exige_admin
def painel_linhas(request):
    dados = arm.linhas()
    jogadores = [_visual(p) for p in arm.jogadores()]
    medias = _medias_da_semana(jogadores)
    forca = forcas([p["id"] for p in jogadores], medias)
    linhas, fora = _montar_linhas(dados["linhas"], jogadores)

    def com_nota(p):
        return {**p, "nota_fmt": formatar(medias[p["id"]]) if medias.get(p["id"]) is not None else "sem nota"}

    blocos = [{
        "numero": i + 1,
        "cor": CORES_LINHAS[i % len(CORES_LINHAS)],
        "jogadores": [com_nota(p) for p in linha],
        "media_fmt": formatar(sum(forca[p["id"]] for p in linha) / len(linha)),
    } for i, linha in enumerate(linhas)]
    sem_nota = [p for p in jogadores if medias.get(p["id"]) is None]
    return render(request, "votacao/painel_linhas.html", {
        "blocos": blocos,
        "fora": [com_nota(p) for p in fora],
        "numeros": range(1, len(blocos) + 1),
        "nova_linha": len(blocos) + 1,
        "total": len(jogadores),
        "por_linha": dados["por_linha"],
        "equilibrar": dados["equilibrar"],
        "publicado": dados["publicado"],
        "data": _data(dados),
        "media_geral": formatar(forca[sem_nota[0]["id"]]) if sem_nota and len(sem_nota) < len(jogadores) else "",
    })


@_exige_admin
@require_POST
def painel_linhas_sortear(request):
    try:
        por_linha = min(max(int(request.POST.get("por_linha", "")), 1), 50)
    except ValueError:
        por_linha = 5
    equilibrar = bool(request.POST.get("equilibrar"))
    jogadores = arm.jogadores()
    if not jogadores:
        messages.error(request, "Ninguém na lista desta semana para sortear.")
        return redirect("votacao:painel_linhas")
    ids = [p["id"] for p in jogadores]
    novas = sortear(ids, forcas(ids, _medias_da_semana(jogadores)), por_linha, equilibrar)
    arm.salvar_sorteio(novas, por_linha, equilibrar)
    messages.success(request, f"🎲 {len(novas)} linha(s) sorteada(s)! Confira, ajuste se quiser e publique.")
    return redirect("votacao:painel_linhas")


@_exige_admin
@require_POST
def painel_linhas_ajustar(request):
    """Cada jogador vem com o número da linha para onde vai (0 = fora, última+1 = nova linha)."""
    jogadores = arm.jogadores()
    linhas, fora = _montar_linhas(arm.linhas()["linhas"], jogadores)
    novas = [[p["id"] for p in linha] for linha in linhas] + [[]]
    movido = None
    for numero_atual, membros in [*enumerate(linhas, start=1), (0, fora)]:
        for p in membros:
            try:
                destino = int(request.POST.get(f"linha_{p['id']}", numero_atual))
            except ValueError:
                continue
            if destino == numero_atual or not 0 <= destino <= len(novas):
                continue
            if numero_atual:
                novas[numero_atual - 1].remove(p["id"])
            if destino:
                novas[destino - 1].append(p["id"])
            movido = (p["id"], destino)
    arm.ajustar_linhas(novas)

    ancora = ""
    if movido:
        pid, destino = movido
        salvas = arm.linhas()["linhas"]
        numero = next((i + 1 for i, linha in enumerate(salvas) if pid in linha), 0)
        ancora = f"#linha-{numero}" if numero else "#fora"
    return redirect(reverse("votacao:painel_linhas") + ancora)


@_exige_admin
@require_POST
def painel_linhas_publicar(request):
    if not arm.linhas()["linhas"]:
        messages.error(request, "Sorteie as linhas antes de publicar.")
    else:
        arm.publicar_linhas()
        messages.success(request, "📣 Linhas publicadas! A galera já vê na aba Linhas da página inicial.")
    return redirect("votacao:painel_linhas")


@_exige_admin
@require_POST
def painel_linhas_despublicar(request):
    arm.despublicar_linhas()
    messages.success(request, "🙈 Linhas escondidas da página inicial. Ajuste o que quiser e publique de novo.")
    return redirect("votacao:painel_linhas")
