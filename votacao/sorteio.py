"""Sorteio das linhas (times) do racha.

Tamanho das linhas (o organizador escolhe um dos dois):
- por jogadores por linha: linhas cheias e a última com quem sobrar (17 com 5 por linha -> 5, 5, 5, 2);
- por número de linhas: o mais igual possível (17 em 3 linhas -> 6, 6, 5).

Sorteio:
1. Embaralha a galera e divide nos tamanhos escolhidos.
2. Se for para equilibrar, troca jogadores entre as linhas enquanto isso deixar as médias mais parecidas,
   e PARA assim que a diferença entre a linha mais forte e a mais fraca ficar dentro do limite
   (`diferenca_max`). Limite maior = para antes = mais sorte; limite 0 = o mais equilibrado possível.
3. Tenta de novo, partindo de outro embaralhamento, até achar um resultado dentro do limite.
   Se nenhum couber no limite, devolve o mais equilibrado que achou (e quem chamou avisa).
"""

import math
import random
from itertools import combinations

TENTATIVAS = 30


def tamanhos(total, por_linha):
    """Linhas cheias de `por_linha` e a última com quem sobrar."""
    if total <= 0:
        return []
    quantidade = math.ceil(total / por_linha)
    return [por_linha] * (quantidade - 1) + [total - por_linha * (quantidade - 1)]


def tamanhos_por_quantidade(total, quantidade):
    """`quantidade` linhas, o mais iguais possível (as primeiras ficam com um a mais)."""
    if total <= 0:
        return []
    quantidade = max(1, min(quantidade, total))
    base, sobra = divmod(total, quantidade)
    return [base + 1] * sobra + [base] * (quantidade - sobra)


def forcas(ids, medias):
    """Força de cada jogador = média das notas; quem não tem nota entra com a média geral."""
    conhecidas = [medias[j] for j in ids if medias.get(j) is not None]
    padrao = sum(conhecidas) / len(conhecidas) if conhecidas else 0
    return {j: medias[j] if medias.get(j) is not None else padrao for j in ids}


def diferenca(linhas, forca):
    """Média da linha mais forte menos a da mais fraca (0 se só há uma linha)."""
    medias = [sum(forca[j] for j in linha) / len(linha) for linha in linhas if linha]
    return max(medias) - min(medias) if len(medias) > 1 else 0.0


def _custo(somas, tams):
    """Quanto as médias das linhas estão diferentes entre si (0 = todas iguais). Guia as trocas."""
    medias = [s / t for s, t in zip(somas, tams)]
    media_geral = sum(medias) / len(medias)
    return sum((m - media_geral) ** 2 for m in medias)


def _espalhamento(somas, tams):
    medias = [s / t for s, t in zip(somas, tams)]
    return max(medias) - min(medias)


def _equilibrar(linhas, forca, alvo):
    """Troca jogadores entre linhas enquanto melhorar, parando assim que a diferença ficar <= alvo."""
    somas = [sum(forca[j] for j in linha) for linha in linhas]
    tams = [len(linha) for linha in linhas]
    if _espalhamento(somas, tams) <= alvo:
        return
    atual = _custo(somas, tams)
    melhorou = True
    while melhorou:
        melhorou = False
        for a, b in combinations(range(len(linhas)), 2):
            la, lb = linhas[a], linhas[b]
            for i in range(len(la)):
                for k in range(len(lb)):
                    diferenca_troca = forca[lb[k]] - forca[la[i]]
                    somas[a] += diferenca_troca
                    somas[b] -= diferenca_troca
                    novo = _custo(somas, tams)
                    if novo < atual - 1e-12:
                        la[i], lb[k] = lb[k], la[i]
                        atual, melhorou = novo, True
                        if _espalhamento(somas, tams) <= alvo:
                            return
                    else:
                        somas[a] -= diferenca_troca
                        somas[b] += diferenca_troca


def sortear(ids, forca, tams, equilibrar=True, diferenca_max=0.0):
    """Devolve (linhas, diferença), em que cada linha é uma lista de ids com os tamanhos `tams`."""
    def dividir():
        embaralhados = random.sample(list(ids), len(ids))
        linhas, inicio = [], 0
        for t in tams:
            linhas.append(embaralhados[inicio:inicio + t])
            inicio += t
        return linhas

    if not ids:
        return [], 0.0
    if not equilibrar or len(tams) < 2:
        linhas = dividir()
        return linhas, diferenca(linhas, forca)

    alvo = max(diferenca_max, 0.0) + 1e-9
    resultados = []
    for _ in range(TENTATIVAS):
        linhas = dividir()
        _equilibrar(linhas, forca, alvo)
        dif = diferenca(linhas, forca)
        if diferenca_max > 0 and dif <= alvo:
            return linhas, dif  # dentro do limite: aceita (quanto maior o limite, mais cedo e mais variado)
        resultados.append((dif, linhas))
    # limite 0, ou nenhum resultado coube no limite: um dos mais equilibrados, ao acaso
    melhor = min(dif for dif, _ in resultados)
    dif, linhas = random.choice([r for r in resultados if r[0] <= melhor + 0.01])
    return linhas, dif
