"""Regras de nota.

- Cada pessoa dá uma nota de 1 a 10 (números inteiros) para cada um dos outros jogadores da semana,
  ou marca "Não conheço" (aí a opinião dela sobre aquele jogador não entra na conta).
- Resultado de cada jogador: média das notas que ele recebeu. Quem recebeu 10 notas ou mais fica sem a
  maior e a menor (uma de cada), para uma nota fora da curva não pesar demais.
- Ordem do ranking: maior média; no empate, quem recebeu a maior nota; se ainda empatar, ordem alfabética.
  Assim cada um tem uma posição só (1º, 2º, 3º...), sem posições repetidas.
- Avaliações feitas antes, na escala de 0 a 5 (sem "escala": 10 no registro), continuam valendo:
  são convertidas na hora para 1 a 10 (0 -> 1, 5 -> 10, proporcional no meio).
"""

from .armazenamento import ordem_alfabetica

ESCALA = 10
OPCOES_NOTA = range(1, ESCALA + 1)  # 1, 2, ..., 10
NAO_CONHECO = "nao-conheco"
DESCARTE_A_PARTIR_DE = 10  # com tantas notas recebidas ou mais, a maior e a menor não entram na média


def escala_nova(registro):
    """A avaliação já foi dada na escala de 1 a 10?"""
    return (registro or {}).get("escala") == ESCALA


def na_escala(valor, registro):
    """A nota na escala de 1 a 10 (converte as avaliações antigas, de 0 a 5)."""
    return valor if escala_nova(registro) else 1 + valor * (ESCALA - 1) / 5


def ler_nota(texto):
    """Converte o valor enviado pelo formulário; devolve None se não for um inteiro de 1 a 10."""
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return None
    return valor if valor in OPCOES_NOTA else None


def contadas(notas):
    """As notas que entram na média: de quem recebeu DESCARTE_A_PARTIR_DE ou mais, sai uma maior e uma menor."""
    if len(notas) < DESCARTE_A_PARTIR_DE:
        return list(notas)
    return sorted(notas)[1:-1]


def _media(valores):
    return sum(valores) / len(valores) if valores else None


def _ordem_do_ranking(linha):
    sem_nota = linha["nota"] is None
    return (
        sem_nota,                                            # quem não tem nota vai para o fim
        0 if sem_nota else -round(linha["nota"], 6),         # 1º: maior média (arredonda p/ empate de verdade)
        0 if sem_nota else -linha["maior"],                  # 2º: maior nota recebida
        ordem_alfabetica(linha["nome"]),                     # 3º: ordem alfabética
    )


def resultados(pessoas, avaliacoes):
    """Uma linha por pessoa, na ordem do ranking (quem não tem nota fica no fim)."""
    ids = {p["id"] for p in pessoas}
    recebidas = {pid: [] for pid in ids}

    for avaliador, registro in avaliacoes.items():
        if avaliador not in ids:  # quem não está na lista recebida (ex.: foi excluído) não conta
            continue
        for pid, valor in registro.get("notas", {}).items():
            if pid in recebidas and pid != avaliador:
                recebidas[pid].append(na_escala(valor, registro))

    linhas = []
    for p in pessoas:
        todas = recebidas[p["id"]]
        valem = contadas(todas)
        linhas.append({
            "pessoa": p,
            "id": p["id"],
            "nome": p["nome"],
            "nota": _media(valem),
            "maior": max(valem, default=None),  # desempate: a maior das notas que contam
            "qtd": len(todas),                  # quantas recebeu (contando as descartadas)
            "descartadas": len(todas) - len(valem),
            "recebidas": todas,
            "contadas": valem,
            "avaliou": avaliacoes.get(p["id"]),
        })
    linhas.sort(key=_ordem_do_ranking)
    return linhas


def formatar(valor):
    return "—" if valor is None else f"{valor:.2f}".replace(".", ",")


def formatar_curto(valor):
    """4.0 -> "4", 2.75 -> "2,75", 3.5 -> "3,5"."""
    return f"{round(valor, 2):g}".replace(".", ",")
