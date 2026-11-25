import time
import zlib
from datetime import datetime
from functools import wraps

from django.contrib import messages
from django.core.cache import cache
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.views.decorators.http import require_POST

from . import armazenamento as arm
from . import trocas
from .notas import NAO_CONHECO, OPCOES_NOTA, formatar, formatar_curto, ler_nota, resultados
from .sorteio import diferenca, forcas, sortear, tamanhos, tamanhos_por_quantidade

# cores do círculo com as iniciais de quem ainda não tem foto (todas com bom contraste para texto branco)
CORES = ["#d1495b", "#00798c", "#30638e", "#6a4c93", "#2b9348", "#c8553d", "#3d5a80", "#b5179e", "#7c6a0a"]
# uma cor para cada linha, como coletes
CORES_LINHAS = ["#2b9254", "#e07a1f", "#2f6fbf", "#c0392b", "#7b4fb3", "#1d9aa5", "#b8860b", "#d63384"]
MEDALHAS = {1: "🥇", 2: "🥈", 3: "🥉"}

# senha de 4 números de cada jogador
SESSAO_JOGADOR = "jogador"
DIAS_CONECTADO = 14      # quem entra com a senha fica conectado; o prazo renova a cada uso (ou "Sair")
MAX_ERROS_SENHA = 5      # tentativas erradas seguidas antes de bloquear aquele jogador
MINUTOS_BLOQUEIO = 10

# Sugestões de troca de linha pela galera (aba Linhas) e a aba "Trocas" do painel para aprovar.
# Desligado: só o organizador ajusta as linhas, na aba Linhas do painel. Para religar, troque para True.
TROCAS_ATIVAS = False


def _visual(pessoa):
    """Acrescenta o que o template precisa para desenhar o avatar (e nunca leva a senha junto)."""
    palavras = pessoa["nome"].split()
    iniciais = palavras[0][0] + (palavras[-1][0] if len(palavras) > 1 else "")
    return {
        **{k: v for k, v in pessoa.items() if k != "senha"},
        "iniciais": iniciais.upper(),
        "cor": CORES[zlib.crc32(pessoa["id"].encode()) % len(CORES)],
        "foto_url": reverse("votacao:foto", args=[pessoa["foto"]]) if pessoa.get("foto") else "",
    }


def _jogador_ou_volta(request, pessoa_id):
    """O jogador como está salvo (com a senha), ou None com aviso se não joga esta semana."""
    pessoa = arm.buscar_jogador(pessoa_id)
    if not pessoa:
        messages.error(request, "Essa pessoa não está na lista desta semana.")
    return pessoa


def _logado_como(request, pessoa):
    """Está conectado como esse jogador, com a senha atual dele e dentro do prazo?"""
    sessao = request.session.get(SESSAO_JOGADOR) or {}
    return (bool(pessoa.get("senha")) and sessao.get("id") == pessoa["id"]
            and sessao.get("senha") == pessoa["senha"] and sessao.get("ate", 0) > time.time())


def _conectar(request, pessoa):
    """Marca (ou renova por mais DIAS_CONECTADO) a conexão do jogador neste celular."""
    request.session[SESSAO_JOGADOR] = {
        "id": pessoa["id"], "senha": pessoa["senha"], "ate": time.time() + DIAS_CONECTADO * 24 * 3600,
    }


def _conectado(request):
    """O jogador conectado neste celular (com a conexão ainda valendo), ou None."""
    pessoa_id = (request.session.get(SESSAO_JOGADOR) or {}).get("id")
    pessoa = arm.buscar_jogador(pessoa_id) if pessoa_id else None
    return pessoa if pessoa and _logado_como(request, pessoa) else None


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
    conectado = _conectado(request)
    pessoas = [{**_visual(p), "avaliou": p["id"] in avaliacoes, "voce": bool(conectado) and p["id"] == conectado["id"]}
               for p in arm.jogadores()]
    return render(request, "votacao/inicio.html", {
        "pessoas": pessoas,
        "conectado": _visual(conectado) if conectado else None,
        "dias_conectado": DIAS_CONECTADO,
    })


def _cor_linha(numero):
    return CORES_LINHAS[(numero - 1) % len(CORES_LINHAS)]


def _linhas_publicadas():
    """Linhas publicadas com quem joga esta semana (listas de jogadores), ou [] se não estiverem publicadas."""
    dados = arm.linhas()
    if not dados["publicado"]:
        return [], dados
    linhas, _ = _montar_linhas(dados["linhas"], [_visual(p) for p in arm.jogadores()])
    return linhas, dados


def ver_linhas(request):
    linhas, dados = _linhas_publicadas()
    return render(request, "votacao/linhas.html", {
        "linhas": [{"numero": i + 1, "cor": _cor_linha(i + 1), "jogadores": linha} for i, linha in enumerate(linhas)],
        "data": _data(dados),
        "trocas_ativas": TROCAS_ATIVAS,
    })


# ---------- sugestões de troca de linha (só quem está conectado, em fila; desligado com TROCAS_ATIVAS) ----------

def sugerir_troca(request):
    """Quem está conectado sugere trocar um jogador de lugar com outro; a sugestão entra na fila do organizador."""
    if not TROCAS_ATIVAS:
        return redirect("votacao:linhas")
    linhas, _ = _linhas_publicadas()
    if not linhas:
        messages.error(request, "As linhas ainda não foram publicadas.")
        return redirect("votacao:linhas")
    conectado = _conectado(request)
    if not conectado:
        # precisa entrar com a própria senha; depois volta para cá
        return render(request, "votacao/trocar.html", {
            "escolher": _por_nome([_visual(p) for p in arm.jogadores()]),
        })
    _conectar(request, conectado)  # usou: renova o prazo de conexão

    ids = [[p["id"] for p in linha] for linha in linhas]
    onde = {j: i + 1 for i, linha in enumerate(ids) for j in linha}
    nomes = {p["id"]: p["nome"] for p in arm.jogadores()}
    erro = None
    escolhido = {"jogador": "", "com": "", "motivo": ""}

    if request.method == "POST":
        escolhido = {campo: request.POST.get(campo, "").strip() for campo in escolhido}
        jogador, com = escolhido["jogador"], escolhido["com"]
        if jogador not in onde:
            erro = "Escolha quem vai trocar de linha."
        elif onde.get(com) in (None, onde[jogador]):
            erro = f"Escolha com quem {nomes[jogador]} troca de lugar (alguém de outra linha)."
        else:
            motivo = " ".join(escolhido["motivo"].split())[:140]
            resultado = arm.pedir_troca(jogador, "troca", com=com, motivo=motivo, por=conectado["id"])
            if resultado == "ok":
                messages.success(request, "🔄 Sugestão enviada! Ela entrou na fila do organizador, que aprova (ou não). "
                                          "Se aprovar, a troca aparece aqui nas linhas.")
            elif resultado == "repetido":
                messages.success(request, "Essa troca já está na fila do organizador. 👍")
            else:
                messages.error(request, "A fila de sugestões está cheia. Fale direto com o organizador.")
            return redirect("votacao:linhas")

    return render(request, "votacao/trocar.html", {
        "conectado": _visual(conectado),
        "linhas": [{"numero": i + 1, "jogadores": linha} for i, linha in enumerate(linhas)],
        "escolhido": escolhido,
        "erro": erro,
    })


def ver_ranking(request):
    """Só a ordem (a média de cada um não aparece). Empates já vêm desfeitos por resultados()."""
    jogadores = [_visual(p) for p in arm.jogadores()]
    avaliacoes = arm.avaliacoes()
    linhas = resultados(jogadores, avaliacoes)
    com_nota = [linha["pessoa"] for linha in linhas if linha["nota"] is not None]
    sem_nota = [linha["pessoa"] for linha in linhas if linha["nota"] is None]
    ranking = [{"posicao": i, "medalha": MEDALHAS.get(i, ""), "pessoa": p} for i, p in enumerate(com_nota, start=1)]
    return render(request, "votacao/ranking.html", {
        "ranking": ranking,
        "sem_nota": sem_nota,
        "total": len(jogadores),
        "ja_avaliaram": sum(1 for p in jogadores if p["id"] in avaliacoes),
    })


def entrar(request, pessoa_id):
    """Pede a senha de 4 números do jogador (para votar, ou para sugerir troca com ?para=trocar)."""
    jogador = _jogador_ou_volta(request, pessoa_id)
    if not jogador:
        return redirect("votacao:inicio")
    # destino depois da senha: só estes dois (nada de endereço vindo de fora)
    para_trocar = TROCAS_ATIVAS and request.GET.get("para") == "trocar"
    destino = reverse("votacao:sugerir_troca") if para_trocar else reverse("votacao:avaliar", args=[pessoa_id])
    if _logado_como(request, jogador):
        return redirect(destino)

    erro = None
    if not jogador["senha"]:
        erro = "Sua senha ainda não foi criada. Peça ao organizador."
    elif request.method == "POST":
        chave = f"senha-erros-{pessoa_id}"
        erros = cache.get(chave, 0)
        if erros >= MAX_ERROS_SENHA:
            erro = f"Muitas tentativas erradas. Espere {MINUTOS_BLOQUEIO} minutos ou peça a senha ao organizador."
        elif constant_time_compare(request.POST.get("senha", "").strip(), jogador["senha"]):
            cache.delete(chave)
            request.session.cycle_key()
            _conectar(request, jogador)
            return redirect(destino)
        else:
            erros += 1
            cache.set(chave, erros, MINUTOS_BLOQUEIO * 60)
            restam = MAX_ERROS_SENHA - erros
            erro = "Senha errada. " + (f"Mais {restam} tentativa{'s' if restam > 1 else ''} antes de bloquear."
                                       if restam else f"Bloqueado por {MINUTOS_BLOQUEIO} minutos.")
    return render(request, "votacao/entrar.html", {
        "pessoa": _visual(jogador), "erro": erro,
        "objetivo": "para sugerir troca de linha" if para_trocar else "para dar e ver as suas notas",
        "voltar": reverse("votacao:sugerir_troca") if para_trocar else reverse("votacao:inicio"),
        "aba": "linhas" if para_trocar else "avaliar",
    })


@require_POST
def sair_jogador(request):
    request.session.pop(SESSAO_JOGADOR, None)
    messages.success(request, "Pronto, você saiu.")
    return redirect("votacao:inicio")


def avaliar(request, pessoa_id):
    jogador = _jogador_ou_volta(request, pessoa_id)
    if not jogador:
        return redirect("votacao:inicio")
    if not _logado_como(request, jogador):  # só o próprio jogador, com a senha dele
        return redirect("votacao:entrar", pessoa_id=pessoa_id)
    _conectar(request, jogador)  # usou: renova o prazo de conexão
    pessoa = _visual(jogador)
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
            messages.success(request, f"Valeu, {pessoa['nome']}! Suas notas foram salvas. 🎉 "
                                      "Você continua conectado neste celular para mudar quando quiser.")
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


def _render_painel(request, template, contexto):
    """render() das telas do painel, com o que o menu precisa (aba Trocas e quantos pedidos estão na fila)."""
    contexto["trocas_ativas"] = TROCAS_ATIVAS
    contexto["trocas_pendentes"] = (sum(1 for p in arm.pedidos() if p["status"] == "pendente")
                                    if TROCAS_ATIVAS else 0)
    return render(request, template, contexto)


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


def _avisar_senhas_criadas(request):
    criadas = arm.garantir_senhas()
    if criadas:
        messages.success(request, f"🔑 Senhas criadas para {criadas} pessoa(s). Elas estão na aba Cadastro.")


@_exige_admin
def painel(request):
    _avisar_senhas_criadas(request)
    linhas = resultados([_visual(p) for p in arm.jogadores()], arm.avaliacoes())
    for linha in linhas:
        linha["nota_fmt"] = formatar(linha["nota"])
        linha["avaliou_data"] = _data(linha["avaliou"])
    return _render_painel(request, "votacao/painel.html", {
        "linhas": linhas,
        "ja_avaliaram": sum(1 for linha in linhas if linha["avaliou"]),
    })


@_exige_admin
def painel_cadastro(request):
    _avisar_senhas_criadas(request)
    return _render_painel(request, "votacao/painel_cadastro.html", {
        "cadastro": [{**_visual(p), "senha": p["senha"]} for p in arm.pessoas()],
        "total_avaliacoes": len(arm.avaliacoes()),
    })


@_exige_admin
@require_POST
def painel_nova_senha(request):
    pessoa = arm.nova_senha(request.POST.get("id", ""))
    if pessoa:
        messages.success(request, f"🔑 Nova senha de {pessoa['nome']}: {pessoa['senha']}. A antiga não funciona mais.")
    return redirect("votacao:painel_cadastro")


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
        partes.append(f"{novos} cadastrado(s) agora (as senhas estão na aba Cadastro)")
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
    return _render_painel(request, "votacao/painel_linhas.html", {
        "blocos": blocos,
        "fora": [com_nota(p) for p in fora],
        "numeros": range(1, len(blocos) + 1),
        "nova_linha": len(blocos) + 1,
        "total": len(jogadores),
        "divisao": dados["divisao"],
        "por_linha": dados["por_linha"],
        "num_linhas": min(dados["num_linhas"], max(len(jogadores), 1)),
        "equilibrar": dados["equilibrar"],
        "diferenca_max": f"{dados['diferenca_max']:g}",  # com ponto, para o <input type=range>
        "diferenca_atual": (formatar(diferenca([[p["id"] for p in linha] for linha in linhas], forca))
                            if len(linhas) > 1 else ""),
        "publicado": dados["publicado"],
        "data": _data(dados),
        "media_geral": formatar(forca[sem_nota[0]["id"]]) if sem_nota and len(sem_nota) < len(jogadores) else "",
    })


def _inteiro(texto, padrao, minimo, maximo):
    try:
        return min(max(int(texto), minimo), maximo)
    except (TypeError, ValueError):
        return padrao


@_exige_admin
@require_POST
def painel_linhas_sortear(request):
    jogadores = arm.jogadores()
    if not jogadores:
        messages.error(request, "Ninguém na lista desta semana para sortear.")
        return redirect("votacao:painel_linhas")
    ids = [p["id"] for p in jogadores]
    anterior = arm.linhas()

    divisao = "num_linhas" if request.POST.get("divisao") == "num_linhas" else "por_linha"
    por_linha = _inteiro(request.POST.get("por_linha"), anterior["por_linha"], 1, 50)
    num_linhas = _inteiro(request.POST.get("num_linhas"), anterior["num_linhas"], 1, len(ids))
    tams = tamanhos_por_quantidade(len(ids), num_linhas) if divisao == "num_linhas" else tamanhos(len(ids), por_linha)
    equilibrar = bool(request.POST.get("equilibrar"))
    try:
        diferenca_max = min(max(float(request.POST.get("diferenca_max", "").replace(",", ".")), 0.0), 5.0)
    except ValueError:
        diferenca_max = anterior["diferenca_max"]

    novas, dif = sortear(ids, forcas(ids, _medias_da_semana(jogadores)), tams, equilibrar, diferenca_max)
    arm.salvar_sorteio(novas, por_linha, equilibrar, divisao=divisao, num_linhas=num_linhas,
                       diferenca_max=diferenca_max, diferenca=dif)

    tamanhos_txt = ", ".join(str(len(linha)) for linha in novas)
    resumo = f"🎲 {len(novas)} linha(s) sorteada(s) ({tamanhos_txt} jogadores)"
    if len(novas) > 1:
        resumo += f". Diferença entre a linha mais forte e a mais fraca: {formatar(dif)}"
        if equilibrar:
            resumo += f" (limite {formatar(diferenca_max)})"
    messages.success(request, resumo + ". Confira, ajuste se quiser e publique.")
    if equilibrar and dif > diferenca_max + 1e-9 and len(novas) > 1:
        messages.error(request, f"Não deu para ficar dentro do limite de {formatar(diferenca_max)}: com essas notas, "
                                f"o mais equilibrado que deu foi {formatar(dif)}.")
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


# ---------- administrador: pedidos de troca de linha ----------

@_exige_admin
def painel_trocas(request):
    if not TROCAS_ATIVAS:
        return redirect("votacao:painel_linhas")
    dados = arm.linhas()
    jogadores = [_visual(p) for p in arm.jogadores()]
    todos = {p["id"]: _visual(p) for p in arm.pessoas()}
    nomes = {pid: p["nome"] for pid, p in todos.items()}
    linhas, _ = _montar_linhas(dados["linhas"], jogadores)
    ids = [[p["id"] for p in linha] for linha in linhas]
    onde = {j: i + 1 for i, linha in enumerate(ids) for j in linha}
    forca = forcas([p["id"] for p in jogadores], _medias_da_semana(jogadores))

    def nome(pid):
        return nomes.get(pid, "alguém que saiu")

    def media(linha):
        return formatar(sum(forca[j] for j in linha) / len(linha)) if linha else "—"

    def lado(celula):
        return {"nome": nome(celula[0]), "situacao": celula[1]} if celula else None

    def cartao(pedido):
        jogador, outro = pedido["jogador"], pedido.get("com")
        troca = pedido["tipo"] == "troca"
        de = onde.get(jogador)
        para = onde.get(outro) if troca else pedido.get("destino")
        novas = trocas.aplicar(ids, pedido)
        # as duas linhas envolvidas, primeiro a de quem pediu: como está e como fica
        envolvidas = [n for n in (de, para) if n] if novas else []
        return {
            "id": pedido["id"],
            "jogador": todos.get(jogador, {"nome": nome(jogador), "iniciais": "?", "cor": "#999"}),
            "outro": todos.get(outro) if troca else None,
            "quem": nome(jogador),
            "no_lugar_de": nome(outro) if troca else "",
            "de": de, "cor_de": _cor_linha(de) if de else "",
            "para": para, "cor_para": _cor_linha(para) if para else "",
            "motivo": pedido.get("motivo", ""),
            "por": nomes.get(pedido.get("por"), "") if pedido.get("por") else "",
            "data": _data(pedido),
            "valido": novas is not None,
            "linhas": [{
                "numero": n,
                "cor": _cor_linha(n),
                "pares": [{"antes": lado(a), "depois": lado(d)} for a, d in trocas.comparar(ids[n - 1], novas[n - 1])],
                "media_antes": media(ids[n - 1]),
                "media_depois": media(novas[n - 1]),
            } for n in envolvidas],
        }

    def resumo(pedido):
        if pedido["tipo"] == "troca":
            return f"{nome(pedido['jogador'])} ⇄ {nome(pedido.get('com'))}"
        return f"{nome(pedido['jogador'])} → Linha {pedido.get('destino')}"

    pendentes = [cartao(p) for p in dados["pedidos"] if p["status"] == "pendente"]
    resolvidos = [{
        "texto": resumo(p),
        "por": nomes.get(p.get("por"), "") if p.get("por") else "",
        "aprovado": p["status"] == "aprovado",
        "quando": _data({"data": p["resolvido_em"]}),
    } for p in reversed(dados["pedidos"]) if p["status"] != "pendente"][:10]
    return _render_painel(request, "votacao/painel_trocas.html", {
        "pendentes": pendentes,
        "resolvidos": resolvidos,
        "tem_linhas": bool(linhas),
        "publicado": dados["publicado"],
    })


@_exige_admin
@require_POST
def painel_troca_decidir(request, pedido_id):
    if not TROCAS_ATIVAS:
        return redirect("votacao:painel_linhas")
    dados = arm.linhas()
    pedido = next((p for p in dados["pedidos"] if p["id"] == pedido_id and p["status"] == "pendente"), None)
    if not pedido:
        messages.error(request, "Esse pedido não está mais pendente.")
        return redirect("votacao:painel_trocas")
    nome = next((p["nome"] for p in arm.pessoas() if p["id"] == pedido["jogador"]), pedido["jogador"])
    if request.POST.get("acao") == "aprovar":
        linhas, _ = _montar_linhas(dados["linhas"], arm.jogadores())
        ids = [[p["id"] for p in linha] for linha in linhas]
        novas = trocas.aplicar(ids, pedido)
        if novas is None:
            messages.error(request, f"Não dá mais para aplicar o pedido de {nome}: as linhas mudaram. "
                                    "Recuse o pedido ou ajuste na aba Linhas.")
            return redirect("votacao:painel_trocas")
        arm.resolver_pedido(pedido_id, True, novas)
        messages.success(request, f"✅ Pedido de {nome} aprovado. As linhas já foram atualizadas.")
    else:
        arm.resolver_pedido(pedido_id, False)
        messages.success(request, f"❌ Pedido de {nome} recusado.")
    return redirect("votacao:painel_trocas")
