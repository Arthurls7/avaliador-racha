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

Regras do organizador:
- Quem cai junto: duplas que têm que ficar na mesma linha. Duplas encadeadas viram um grupo
  (Ana+Bia e Bia+Caio = os três juntos). Cada grupo vai inteiro para uma linha e o equilíbrio não o separa.
- Quem não cai junto: duplas que têm que ficar em linhas diferentes. Ninguém é posto, nem trocado,
  para uma linha onde está alguém de quem tem que ficar separado.
"""

import math
import random
from itertools import combinations

TENTATIVAS = 30


class SorteioImpossivel(Exception):
    """As regras não deixam sortear com esses tamanhos de linha."""


class GrupoNaoCabe(SorteioImpossivel):
    """Um grupo de "quem cai junto" não cabe em nenhuma linha (ex.: 3 juntos com linhas de 2)."""

    def __init__(self, grupo):
        super().__init__(grupo)
        self.grupo = grupo


class RegrasEmConflito(SorteioImpossivel):
    """Duas pessoas têm que cair juntas (direto ou por um grupo) e, ao mesmo tempo, separadas."""

    def __init__(self, a, b):
        super().__init__(a, b)
        self.a, self.b = a, b


class NaoDeuParaSeparar(SorteioImpossivel):
    """Não há linhas suficientes para separar todo mundo de "quem não cai junto"."""


def agrupar(ids, juntos):
    """Junta as duplas em grupos (só com quem está em `ids`). Devolve os grupos de 2 ou mais pessoas."""
    pai = {j: j for j in ids}

    def raiz(j):
        while pai[j] != j:
            pai[j] = pai[pai[j]]
            j = pai[j]
        return j

    for a, b in juntos:
        if a in pai and b in pai:
            pai[raiz(a)] = raiz(b)
    grupos = {}
    for j in ids:
        grupos.setdefault(raiz(j), []).append(j)
    return [grupo for grupo in grupos.values() if len(grupo) > 1]


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


def _equilibrar(linhas, forca, alvo, fixos=frozenset(), inimigos=None):
    """Troca jogadores entre linhas enquanto melhorar, parando assim que a diferença ficar <= alvo.

    Quem está em `fixos` (faz parte de um grupo de "quem cai junto") não é trocado, e nenhuma troca põe
    alguém numa linha com quem ele tem que ficar separado (`inimigos`).
    """
    inimigos = inimigos or {}

    def pode(x, y, destino_x, destino_y):
        """x vai para a linha destino_x (de onde sai y), e y para destino_y (de onde sai x)."""
        return (not any(z in inimigos.get(x, ()) for z in destino_x if z != y)
                and not any(z in inimigos.get(y, ()) for z in destino_y if z != x))

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
                    if la[i] in fixos or lb[k] in fixos or not pode(la[i], lb[k], lb, la):
                        continue
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


def _dividir(ids, tams, grupos, inimigos):
    """Embaralha e divide nos tamanhos `tams` respeitando as regras. None se não deu desta vez."""
    linhas = [[] for _ in tams]
    agrupados = {j for grupo in grupos for j in grupo}
    # primeiro quem tem regra (grupos maiores antes, que são os mais difíceis de encaixar), cada um numa
    # linha com lugar e sem ninguém de quem tenha que ficar separado; depois o resto, ao acaso
    com_regra = sorted(random.sample(grupos, len(grupos)), key=len, reverse=True)
    com_regra += [[j] for j in random.sample(list(ids), len(ids)) if j in inimigos and j not in agrupados]
    for unidade in com_regra:
        separar_de = set().union(*(inimigos.get(j, ()) for j in unidade))
        cabe = [i for i, t in enumerate(tams)
                if t - len(linhas[i]) >= len(unidade) and not separar_de.intersection(linhas[i])]
        if not cabe:
            return None
        linhas[random.choice(cabe)].extend(unidade)
    ja_postos = {j for linha in linhas for j in linha}
    livres = [j for j in ids if j not in ja_postos]
    livres = random.sample(livres, len(livres))
    for i, t in enumerate(tams):
        falta = t - len(linhas[i])
        linhas[i].extend(livres[:falta])
        livres = livres[falta:]
    return linhas


def sortear(ids, forca, tams, equilibrar=True, diferenca_max=0.0, juntos=(), separados=()):
    """Devolve (linhas, diferença), em que cada linha é uma lista de ids com os tamanhos `tams`.

    `juntos` são duplas que têm que cair na mesma linha e `separados`, duplas que têm que cair em linhas
    diferentes. Levanta SorteioImpossivel (GrupoNaoCabe, RegrasEmConflito ou NaoDeuParaSeparar) se não der.
    """
    if not ids:
        return [], 0.0
    presentes = set(ids)
    grupos = agrupar(list(ids), juntos)
    grupo_de = {j: i for i, grupo in enumerate(grupos) for j in grupo}
    inimigos = {}
    for a, b in separados:
        if a in presentes and b in presentes and a != b:
            if a in grupo_de and grupo_de.get(b) == grupo_de[a]:
                raise RegrasEmConflito(a, b)
            inimigos.setdefault(a, set()).add(b)
            inimigos.setdefault(b, set()).add(a)
    maior = max(grupos, key=len, default=[])
    if len(maior) > max(tams):
        raise GrupoNaoCabe(maior)
    fixos = frozenset(grupo_de)

    def dividir():
        # a divisão é ao acaso; com regras, pode precisar de algumas tentativas para encaixar todo mundo
        for _ in range(200 if inimigos else 50):
            linhas = _dividir(ids, tams, grupos, inimigos)
            if linhas is not None:
                return linhas
        if inimigos:
            raise NaoDeuParaSeparar()
        raise GrupoNaoCabe(maior)

    if not equilibrar or len(tams) < 2:
        linhas = dividir()
        return linhas, diferenca(linhas, forca)

    alvo = max(diferenca_max, 0.0) + 1e-9
    resultados = []
    for _ in range(TENTATIVAS):
        linhas = dividir()
        _equilibrar(linhas, forca, alvo, fixos, inimigos)
        dif = diferenca(linhas, forca)
        if diferenca_max > 0 and dif <= alvo:
            return linhas, dif  # dentro do limite: aceita (quanto maior o limite, mais cedo e mais variado)
        resultados.append((dif, linhas))
    # limite 0, ou nenhum resultado coube no limite: um dos mais equilibrados, ao acaso
    melhor = min(dif for dif, _ in resultados)
    dif, linhas = random.choice([r for r in resultados if r[0] <= melhor + 0.01])
    return linhas, dif
