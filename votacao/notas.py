"""Regras de nota.

- Cada pessoa dá uma nota de 0 a 5 (números inteiros) para cada um dos outros jogadores da semana,
  ou marca "Não conheço" (aí a opinião dela sobre aquele jogador não entra na conta).
- Resultado de cada jogador: média das notas que ele recebeu.
- Ordem do ranking: maior média; no empate, quem recebeu a maior nota; se ainda empatar, ordem alfabética.
  Assim cada um tem uma posição só (1º, 2º, 3º...), sem posições repetidas.
"""

from .armazenamento import ordem_alfabetica

OPCOES_NOTA = range(6)  # 0, 1, 2, 3, 4, 5
NAO_CONHECO = "nao-conheco"


def ler_nota(texto):
    """Converte o valor enviado pelo formulário; devolve None se não for um inteiro de 0 a 5."""
    try:
        valor = int(texto)
    except (TypeError, ValueError):
        return None
    return valor if valor in OPCOES_NOTA else None


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
        "maior": max(recebidas[p["id"]], default=None),
        "qtd": len(recebidas[p["id"]]),
        "avaliou": avaliacoes.get(p["id"]),
    } for p in pessoas]
    linhas.sort(key=_ordem_do_ranking)
    return linhas


def formatar(valor):
    return "—" if valor is None else f"{valor:.2f}".replace(".", ",")


def formatar_curto(valor):
    """4.0 -> "4", 2.75 -> "2,75", 3.5 -> "3,5"."""
    return f"{round(valor, 2):g}".replace(".", ",")
