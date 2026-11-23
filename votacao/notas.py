"""Regras de nota.

- Cada pessoa dá uma nota de 0 a 5 (números inteiros) para cada um dos outros jogadores da semana.
- Resultado de cada jogador: média das notas que ele recebeu.
"""

OPCOES_NOTA = range(6)  # 0, 1, 2, 3, 4, 5


def ler_nota(texto):
    """Converte o valor enviado pelo formulário; devolve None se não for um inteiro de 0 a 5."""
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return None
    return valor if valor in OPCOES_NOTA else None


def _media(valores):
    return sum(valores) / len(valores) if valores else None


def resultados(pessoas, avaliacoes):
    """Uma linha por pessoa, da maior nota para a menor (quem não tem nota fica no fim)."""
    ids = {p["id"] for p in pessoas}
    recebidas = {pid: [] for pid in ids}

    for avaliador, registro in avaliacoes.items():
        if avaliador not in ids:  # quem não joga esta semana não conta
            continue
        for pid, valor in registro.get("notas", {}).items():
            if pid in recebidas and pid != avaliador:
                recebidas[pid].append(valor)

    linhas = [{
        "pessoa": p,
        "id": p["id"],
        "nome": p["nome"],
        "nota": _media(recebidas[p["id"]]),
        "qtd": len(recebidas[p["id"]]),
        "avaliou": avaliacoes.get(p["id"]),
    } for p in pessoas]
    linhas.sort(key=lambda l: (l["nota"] is None, -(l["nota"] or 0)))
    return linhas


def formatar(valor):
    return "—" if valor is None else f"{valor:.2f}".replace(".", ",")
