"""Sorteio das linhas (times) do racha.

1. Embaralha a galera e divide em linhas de `por_linha` jogadores (a última fica com quem sobrar).
2. Se for para equilibrar, troca jogadores entre as linhas enquanto isso deixar as médias mais parecidas.
3. Faz isso algumas vezes e escolhe, ao acaso, um dos resultados mais equilibrados, para que cada
   "sortear de novo" dê linhas diferentes.
"""

import math
import random
from itertools import combinations

TENTATIVAS = 12


def tamanhos(total, por_linha):
    if total <= 0:
        return []
    quantidade = math.ceil(total / por_linha)
    return [por_linha] * (quantidade - 1) + [total - por_linha * (quantidade - 1)]


def forcas(ids, medias):
    """Força de cada jogador = média das notas; quem não tem nota entra com a média geral."""
    conhecidas = [medias[j] for j in ids if medias.get(j) is not None]
    padrao = sum(conhecidas) / len(conhecidas) if conhecidas else 0
    return {j: medias[j] if medias.get(j) is not None else padrao for j in ids}


def _custo(somas, tams):
    """Quanto as médias das linhas estão diferentes entre si (0 = todas iguais)."""
    medias = [s / t for s, t in zip(somas, tams)]
    media_geral = sum(medias) / len(medias)
    return sum((m - media_geral) ** 2 for m in medias)


def _equilibrar(linhas, forca):
    somas = [sum(forca[j] for j in linha) for linha in linhas]
    tams = [len(linha) for linha in linhas]
    atual = _custo(somas, tams)
    melhorou = True
    while melhorou:
        melhorou = False
        for a, b in combinations(range(len(linhas)), 2):
            la, lb = linhas[a], linhas[b]
            for i in range(len(la)):
                for k in range(len(lb)):
                    diferenca = forca[lb[k]] - forca[la[i]]
                    somas[a] += diferenca
                    somas[b] -= diferenca
                    novo = _custo(somas, tams)
                    if novo < atual - 1e-12:
                        la[i], lb[k] = lb[k], la[i]
                        atual, melhorou = novo, True
                    else:
                        somas[a] -= diferenca
                        somas[b] += diferenca
    return atual


def sortear(ids, forca, por_linha, equilibrar=True):
    """Devolve uma lista de linhas (cada linha é uma lista de ids)."""
    tams = tamanhos(len(ids), por_linha)

    def dividir():
        embaralhados = random.sample(list(ids), len(ids))
        linhas, inicio = [], 0
        for t in tams:
            linhas.append(embaralhados[inicio:inicio + t])
            inicio += t
        return linhas

    if not equilibrar or len(tams) < 2:
        return dividir()

    resultados = []
    for _ in range(TENTATIVAS):
        linhas = dividir()
        resultados.append((_equilibrar(linhas, forca), linhas))
    resultados.sort(key=lambda r: r[0])
    return random.choice(resultados[:3])[1]
