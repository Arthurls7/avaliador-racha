import zlib
from datetime import datetime
from functools import wraps

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import armazenamento as arm
from .notas import OPCOES_NOTA, formatar, ler_nota, resultados

# cores do círculo com as iniciais de quem ainda não tem foto (todas com bom contraste para texto branco)
CORES = ["#d1495b", "#00798c", "#30638e", "#6a4c93", "#2b9348", "#c8553d", "#3d5a80", "#b5179e", "#7c6a0a"]


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


def avaliar(request, pessoa_id):
    pessoa = _jogador_ou_volta(request, pessoa_id)
    if not pessoa:
        return redirect("votacao:inicio")
    outros = _outros(pessoa_id)
    anterior = arm.avaliacoes().get(pessoa_id)
    # avaliações antigas (ranking, ataque/defesa) podem ter notas quebradas; só preenche as inteiras
    notas = {pid: int(v) for pid, v in (anterior or {}).get("notas", {}).items() if v in OPCOES_NOTA}
    erro = None

    if request.method == "POST" and outros:
        notas, faltando = {}, []
        for p in outros:
            valor = ler_nota(request.POST.get(f"nota_{p['id']}"))
            if valor is None:
                faltando.append(p["nome"])
            else:
                notas[p["id"]] = valor
        if not faltando:
            arm.salvar_avaliacao(pessoa_id, {"notas": notas})
            messages.success(request, f"Valeu, {pessoa['nome']}! Suas notas foram salvas. 🎉")
            return redirect("votacao:inicio")
        erro = "Faltou dar nota para: " + ", ".join(faltando) + "."

    return render(request, "votacao/avaliar.html", {
        "pessoa": pessoa,
        "linhas": [{"pessoa": p, "nota": notas.get(p["id"])} for p in outros],
        "opcoes": OPCOES_NOTA,
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
    cadastro = [_visual(p) for p in arm.pessoas()]
    linhas = resultados([p for p in cadastro if p["joga"]], arm.avaliacoes())
    for linha in linhas:
        linha["nota_fmt"] = formatar(linha["nota"])
        linha["avaliou_data"] = _data(linha["avaliou"])
    return render(request, "votacao/painel.html", {
        "linhas": linhas,
        "ja_avaliaram": sum(1 for linha in linhas if linha["avaliou"]),
        "cadastro": cadastro,
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
def painel_cadastro(request):
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
    return redirect("votacao:painel")


@_exige_admin
@require_POST
def painel_remover_foto(request):
    arm.remover_foto(request.POST.get("id", ""))
    messages.success(request, "Foto removida.")
    return redirect("votacao:painel")


@_exige_admin
@require_POST
def painel_excluir(request):
    pessoa = arm.excluir_pessoa(request.POST.get("id", ""))
    if pessoa:
        messages.success(request, f"{pessoa['nome']} foi excluído(a) do cadastro.")
    return redirect("votacao:painel")


@_exige_admin
@require_POST
def painel_zerar(request):
    arm.zerar_avaliacoes()
    messages.success(request, "Todas as avaliações foram apagadas.")
    return redirect("votacao:painel")
