"""Tags divertidas e setas de sobe/desce do ranking público (sem mostrar a média de ninguém).

Tags, na ordem em que aparecem (no pódio só a primeira, na lista as primeiras MAX_TAGS_NA_LISTA):
- 👑 Craque da galera: 1º lugar (ou "Segue no topo", se já era o 1º da última semana fechada);
- 🏅 Campeão da última semana: foi o 1º na última semana fechada;
- 🚀 Em alta / 📉 Em baixa: subiu / caiu MUDANCA posições ou mais desde a última semana fechada;
- 🏮 Pior do racha: o último do ranking (só quando o último está fora do pódio);
- 🔥 Quente: todas as notas acima de 7; 😐 Mediano: todas de 4 a 7; 🧊 Gelado: todas abaixo de 4;
- 🎯 Unanimidade: recebeu notas bem parecidas; 🌶️ Polêmico: notas muito diferentes entre si;
- 🌟 Todo mundo conhece: recebeu nota de todos que votaram; 🕵️ Misterioso: de menos da metade deles;
- 🗳️ Já votou.
"""

from statistics import pstdev

MIN_NOTAS = 3        # para as tags que olham as notas recebidas fazerem sentido
QUENTE = 7           # Quente: todas as notas acima disso
MEDIANO = (4, 7)     # Mediano: todas as notas nesse intervalo (inclusive)
GELADO = 4           # Gelado: todas as notas abaixo disso
UNANIMIDADE = 1.0    # desvio das notas de até 1 ponto (escala de 1 a 10)
POLEMICO = 2.5       # desvio de 2,5 pontos ou mais
MUDANCA = 3          # posições que precisa subir (🚀) ou cair (📉)
MAX_TAGS_NA_LISTA = 4


def _tag(emoji, texto, tipo):
    return {"emoji": emoji, "texto": texto, "tipo": tipo}


def _movimento(antes, agora):
    delta = antes - agora
    if delta > 0:
        return {"tipo": "sobe", "texto": f"▲{delta}", "titulo": f"Subiu {delta} desde a última semana"}
    if delta < 0:
        return {"tipo": "desce", "texto": f"▼{-delta}", "titulo": f"Caiu {-delta} desde a última semana"}
    return {"tipo": "igual", "texto": "=", "titulo": "Mesma posição da última semana"}


def _faixa(notas):
    """Quente, Mediano ou Gelado, se todas as notas caem na mesma faixa."""
    if min(notas) > QUENTE:
        return _tag("🔥", "Quente", "quente")
    if MEDIANO[0] <= min(notas) and max(notas) <= MEDIANO[1]:
        return _tag("😐", "Mediano", "neutro")
    if max(notas) < GELADO:
        return _tag("🧊", "Gelado", "frio")
    return None


def destaques(ranking, votaram, semanas):
    """Tags e movimento de cada um do ranking.

    `ranking`: linhas de resultados() que têm nota, já na ordem; `votaram`: ids de quem votou esta semana;
    `semanas`: o histórico (da mais antiga para a mais nova). Devolve {id: {"tags": [...], "mov": {...} | None}}.
    """
    ultima = semanas[-1] if semanas else None
    antes = {r["id"]: i for i, r in enumerate(ultima["ranking"], start=1)} if ultima else {}
    campeao = ultima["ranking"][0]["id"] if ultima and ultima["ranking"] else None
    total = len(ranking)

    saida = {}
    for posicao, linha in enumerate(ranking, start=1):
        pid, notas, tags, mov = linha["id"], linha["recebidas"], [], None
        if posicao == 1:
            tags.append(_tag("👑", "Segue no topo" if pid == campeao else "Craque da galera", "ouro"))
        elif pid == campeao:
            tags.append(_tag("🏅", "Campeão da última semana", "ouro"))
        if pid in antes:
            mov = _movimento(antes[pid], posicao)
            if antes[pid] - posicao >= MUDANCA:
                tags.append(_tag("🚀", "Em alta", "quente"))
            elif posicao - antes[pid] >= MUDANCA:
                tags.append(_tag("📉", "Em baixa", "frio"))
        if posicao == total and total > 3:
            tags.append(_tag("🏮", "Pior do racha", "lanterna"))
        if len(notas) >= MIN_NOTAS:
            faixa = _faixa(notas)
            if faixa:
                tags.append(faixa)
            desvio = pstdev(notas)
            if desvio <= UNANIMIDADE:
                tags.append(_tag("🎯", "Unanimidade", "alvo"))
            elif desvio >= POLEMICO:
                tags.append(_tag("🌶️", "Polêmico", "quente"))
        outros_que_votaram = len(votaram - {pid})
        if outros_que_votaram >= MIN_NOTAS:
            if linha["qtd"] >= outros_que_votaram:
                tags.append(_tag("🌟", "Todo mundo conhece", "alvo"))
            elif linha["qtd"] * 2 < outros_que_votaram:
                tags.append(_tag("🕵️", "Misterioso", "neutro"))
        if pid in votaram:
            tags.append(_tag("🗳️", "Já votou", "suave"))
        saida[pid] = {"tags": tags, "mov": mov}
    return saida
