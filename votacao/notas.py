"""Regras de nota.

- Ataque e defesa: notas de 0 a 5; a nota final é a média das duas.
- Ranking (melhor -> pior): o 1º recebe 5, o último 0,5 e os do meio ficam
  igualmente espaçados entre esses dois valores.
- Resultado de cada pessoa: média das notas que ela recebeu de todo mundo.
"""

NOTA_MAX = 5.0
NOTA_MIN_RANKING = 0.5
OPCOES_NOTA = [i / 2 for i in range(11)]  # 0, 0.5, 1, ..., 5


def notas_por_ranking(ordem):
    n = len(ordem)
    if n == 1:
        return {ordem[0]: NOTA_MAX}
    passo = (NOTA_MAX - NOTA_MIN_RANKING) / (n - 1)
    return {pid: round(NOTA_MAX - i * passo, 4) for i, pid in enumerate(ordem)}


def media_ataque_defesa(ataque, defesa):
    return (ataque + defesa) / 2


def _media(valores):
    return sum(valores) / len(valores) if valores else None


def resultados(pessoas, avaliacoes):
    """Uma linha por pessoa, da maior nota final para a menor (sem nota no fim)."""
    ids = {p["id"] for p in pessoas}
    recebidas = {pid: {"notas": [], "ataque": [], "defesa": []} for pid in ids}

    for avaliador, registro in avaliacoes.items():
        if avaliador not in ids:  # quem saiu da lista não conta mais
            continue
        for campo in ("notas", "ataque", "defesa"):
            for pid, valor in registro.get(campo, {}).items():
                if pid in recebidas and pid != avaliador:
                    recebidas[pid][campo].append(valor)

    linhas = []
    for p in pessoas:
        r = recebidas[p["id"]]
        avaliacao_feita = avaliacoes.get(p["id"])
        linhas.append({
            "id": p["id"],
            "nome": p["nome"],
            "nota": _media(r["notas"]),
            "qtd": len(r["notas"]),
            "ataque": _media(r["ataque"]),
            "defesa": _media(r["defesa"]),
            "avaliou": avaliacao_feita,
        })
    linhas.sort(key=lambda l: (l["nota"] is None, -(l["nota"] or 0)))
    return linhas


def formatar(valor):
    return "—" if valor is None else f"{valor:.2f}".replace(".", ",")
