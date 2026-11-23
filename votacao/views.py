from datetime import datetime
from functools import wraps

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import armazenamento as arm
from .notas import OPCOES_NOTA, formatar, media_ataque_defesa, notas_por_ranking, resultados

MODOS = {"ranking": "Ranking", "ataque_defesa": "Ataque e defesa"}


def _pessoa_ou_404(pessoa_id):
    pessoa = arm.buscar_pessoa(pessoa_id)
    if not pessoa:
        raise Http404("Pessoa não encontrada.")
    return pessoa


def _outros(pessoa_id):
    return [p for p in arm.pessoas() if p["id"] != pessoa_id]


def _data(registro):
    if not registro or not registro.get("data"):
        return ""
    return datetime.fromisoformat(registro["data"]).strftime("%d/%m às %H:%M")


def _texto_nota(valor):
    return None if valor is None else f"{valor:g}"


def _ler_nota(texto):
    try:
        valor = float((texto or "").replace(",", "."))
    except ValueError:
        return None
    return valor if 0 <= valor <= 5 else None


# ---------- quem vai avaliar ----------

def inicio(request):
    avaliacoes = arm.avaliacoes()
    pessoas = [{**p, "avaliou": p["id"] in avaliacoes} for p in arm.pessoas()]
    return render(request, "votacao/inicio.html", {"pessoas": pessoas})


def escolher_modo(request, pessoa_id):
    pessoa = _pessoa_ou_404(pessoa_id)
    anterior = arm.avaliacoes().get(pessoa_id)
    return render(request, "votacao/escolher_modo.html", {
        "pessoa": pessoa,
        "tem_outros": bool(_outros(pessoa_id)),
        "anterior": anterior,
        "anterior_modo": MODOS.get(anterior["modo"]) if anterior else "",
        "anterior_data": _data(anterior),
    })


def avaliar_ranking(request, pessoa_id):
    pessoa = _pessoa_ou_404(pessoa_id)
    outros = _outros(pessoa_id)
    if not outros:
        return redirect("votacao:escolher_modo", pessoa_id=pessoa_id)
    anterior = arm.avaliacoes().get(pessoa_id)

    if request.method == "POST":
        ordem = request.POST.getlist("ranking")
        if len(ordem) != len(outros) or set(ordem) != {p["id"] for p in outros}:
            messages.error(request, "O ranking precisa ter todo mundo da lista, uma vez cada. Confira e salve de novo.")
            return redirect("votacao:ranking", pessoa_id=pessoa_id)
        arm.salvar_avaliacao(pessoa_id, {
            "modo": "ranking",
            "ranking": ordem,
            "notas": notas_por_ranking(ordem),
        })
        messages.success(request, f"Valeu, {pessoa['nome']}! Seu ranking foi salvo.")
        return redirect("votacao:inicio")

    ranking_anterior = anterior["ranking"] if anterior and anterior["modo"] == "ranking" else []
    return render(request, "votacao/ranking.html", {
        "pessoa": pessoa,
        "anterior": anterior,
        "dados_js": {"pessoas": outros, "ranking": ranking_anterior},
    })


def avaliar_notas(request, pessoa_id):
    pessoa = _pessoa_ou_404(pessoa_id)
    outros = _outros(pessoa_id)
    if not outros:
        return redirect("votacao:escolher_modo", pessoa_id=pessoa_id)
    anterior = arm.avaliacoes().get(pessoa_id)

    ataque, defesa, erro = {}, {}, None
    if anterior and anterior["modo"] == "ataque_defesa":
        ataque, defesa = anterior["ataque"], anterior["defesa"]

    if request.method == "POST":
        ataque, defesa, faltando = {}, {}, []
        for p in outros:
            a = _ler_nota(request.POST.get(f"ataque_{p['id']}"))
            d = _ler_nota(request.POST.get(f"defesa_{p['id']}"))
            if a is not None:
                ataque[p["id"]] = a
            if d is not None:
                defesa[p["id"]] = d
            if a is None or d is None:
                faltando.append(p["nome"])
        if not faltando:
            arm.salvar_avaliacao(pessoa_id, {
                "modo": "ataque_defesa",
                "ataque": ataque,
                "defesa": defesa,
                "notas": {pid: media_ataque_defesa(ataque[pid], defesa[pid]) for pid in ataque},
            })
            messages.success(request, f"Valeu, {pessoa['nome']}! Suas notas foram salvas.")
            return redirect("votacao:inicio")
        erro = "Faltou dar nota de ataque e defesa para: " + ", ".join(faltando) + "."

    linhas = [{
        "pessoa": p,
        "ataque": _texto_nota(ataque.get(p["id"])),
        "defesa": _texto_nota(defesa.get(p["id"])),
    } for p in outros]
    opcoes = [(_texto_nota(v), _texto_nota(v).replace(".", ",")) for v in OPCOES_NOTA]
    return render(request, "votacao/notas.html", {
        "pessoa": pessoa,
        "anterior": anterior,
        "linhas": linhas,
        "opcoes": opcoes,
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
    pessoas = arm.pessoas()
    linhas = resultados(pessoas, arm.avaliacoes())
    for linha in linhas:
        linha["nota_fmt"] = formatar(linha["nota"])
        linha["ataque_fmt"] = formatar(linha["ataque"])
        linha["defesa_fmt"] = formatar(linha["defesa"])
        if linha["avaliou"]:
            linha["avaliou_modo"] = MODOS.get(linha["avaliou"]["modo"], "")
            linha["avaliou_data"] = _data(linha["avaliou"])
    return render(request, "votacao/painel.html", {
        "linhas": linhas,
        "ja_avaliaram": sum(1 for linha in linhas if linha["avaliou"]),
        "texto_nomes": "\n".join(p["nome"] for p in pessoas),
    })


@_exige_admin
@require_POST
def painel_pessoas(request):
    lista = arm.definir_pessoas(arm.limpar_nomes(request.POST.get("nomes", "")))
    messages.success(request, f"Lista salva com {len(lista)} pessoa(s).")
    return redirect("votacao:painel")


@_exige_admin
@require_POST
def painel_zerar(request):
    arm.zerar_avaliacoes()
    messages.success(request, "Todas as avaliações foram apagadas.")
    return redirect("votacao:painel")
