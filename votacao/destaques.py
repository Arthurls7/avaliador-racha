"""Tags divertidas e setas de sobe/desce do ranking público (sem mostrar a média de ninguém).

Tags:
- 👑 Craque da galera: 1º lugar (ou "Segue no topo", se já era o 1º da última semana fechada);
- 🏅 Campeão da última semana: foi o 1º na última semana fechada;
- 🚀 Em alta: subiu EM_ALTA posições ou mais desde a última semana fechada;
- 🆕 Estreante: nunca apareceu no histórico (só quando já existe histórico);
- 🎯 Unanimidade: recebeu notas bem parecidas; 🌶️ Polêmico: notas muito diferentes entre si;
- 🌟 Todo mundo conhece: recebeu nota de todos que votaram (ninguém marcou "não conheço");
- 🗳️ Já votou.
"""

from statistics import pstdev

MIN_NOTAS = 3        # para Unanimidade/Polêmico/Todo mundo conhece fazerem sentido
UNANIMIDADE = 1.0    # desvio das notas de até 1 ponto (escala de 1 a 10)
POLEMICO = 2.5       # desvio de 2,5 pontos ou mais
EM_ALTA = 3          # posições que precisa subir para ganhar o 🚀


def _tag(emoji, texto, tipo):
    return {"emoji": emoji, "texto": texto, "tipo": tipo}


def _movimento(antes, agora):
    delta = antes - agora
    if delta > 0:
        return {"tipo": "sobe", "texto": f"▲{delta}", "titulo": f"Subiu {delta} desde a última semana"}
    if delta < 0:
        return {"tipo": "desce", "texto": f"▼{-delta}", "titulo": f"Caiu {-delta} desde a última semana"}
    return {"tipo": "igual", "texto": "=", "titulo": "Mesma posição da última semana"}


def destaques(ranking, votaram, semanas):
    """Tags e movimento de cada um do ranking.

    `ranking`: linhas de resultados() que têm nota, já na ordem; `votaram`: ids de quem votou esta semana;
    `semanas`: o histórico (da mais antiga para a mais nova). Devolve {id: {"tags": [...], "mov": {...} | None}}.
    """
    ultima = semanas[-1] if semanas else None
    antes = {r["id"]: i for i, r in enumerate(ultima["ranking"], start=1)} if ultima else {}
    campeao = ultima["ranking"][0]["id"] if ultima and ultima["ranking"] else None
    vistos = {r["id"] for s in semanas for r in s["ranking"]}
    vistos |= {x["id"] for s in semanas for x in s.get("sem_nota", []) if isinstance(x, dict)}

    saida = {}
    for posicao, linha in enumerate(ranking, start=1):
        pid, tags, mov = linha["id"], [], None
        if posicao == 1:
            tags.append(_tag("👑", "Segue no topo" if pid == campeao else "Craque da galera", "ouro"))
        elif pid == campeao:
            tags.append(_tag("🏅", "Campeão da última semana", "ouro"))
        if pid in antes:
            mov = _movimento(antes[pid], posicao)
            if antes[pid] - posicao >= EM_ALTA:
                tags.append(_tag("🚀", "Em alta", "quente"))
        elif ultima and pid not in vistos:
            tags.append(_tag("🆕", "Estreante", "novo"))
        if len(linha["recebidas"]) >= MIN_NOTAS:
            desvio = pstdev(linha["recebidas"])
            if desvio <= UNANIMIDADE:
                tags.append(_tag("🎯", "Unanimidade", "alvo"))
            elif desvio >= POLEMICO:
                tags.append(_tag("🌶️", "Polêmico", "quente"))
        outros_que_votaram = len(votaram - {pid})
        if outros_que_votaram >= MIN_NOTAS and linha["qtd"] >= outros_que_votaram:
            tags.append(_tag("🌟", "Todo mundo conhece", "alvo"))
        if pid in votaram:
            tags.append(_tag("🗳️", "Já votou", "suave"))
        saida[pid] = {"tags": tags, "mov": mov}
    return saida
