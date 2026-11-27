"""Tags divertidas e setas de sobe/desce do ranking público (sem mostrar a média de ninguém).

Cada um pode ter várias tags, mas só aparecem MAX_TAGS: as mais raras da semana (as que menos gente tem).
Empate: vale a ordem de ORDEM, das tags mais raras por natureza para as mais comuns. "Já votou" só
aparece quando a pessoa não tem nenhuma outra.

- 👑 Craque da galera: 1º lugar (ou "Segue no topo", se já era o 1º da última semana fechada);
- 🏮 Pior do racha: o último do ranking (só quando o último está fora do pódio);
- 🏅 Campeão da última semana: foi o 1º na última semana fechada;
- 😮‍💨 Escapou por pouco: logo acima da zona de rebaixamento (fora do pódio);
- 🔪 Carrasco / 🍬 Coração mole: mais da metade das notas que deu foram 4 ou menos / 8 ou mais;
- 🌶️ Polêmico: recebeu notas muito diferentes entre si; 🎯 Unanimidade: notas bem parecidas;
- 🚀 Em alta / 📉 Em baixa: subiu / caiu MUDANCA posições ou mais desde a última semana fechada;
- 🕵️ Misterioso: recebeu nota de menos da metade de quem votou;
- 🔥 Quente: todas as notas acima de 7; 😐 Mediano: todas de 4 a 7; 🧊 Gelado: todas abaixo de 4;
- 💯 Já levou 10: recebeu pelo menos um 10 (mesmo que tenha saído da média por ser a maior);
- 🗳️ Já votou.
"""

from collections import Counter
from statistics import pstdev

ZONA_REBAIXAMENTO = 4  # os últimos do ranking que ficam na zona de rebaixamento
MIN_NOTAS = 3          # para as tags que olham várias notas fazerem sentido
QUENTE = 7             # Quente: todas as notas acima disso
MEDIANO = (4, 7)       # Mediano: todas as notas nesse intervalo (inclusive)
GELADO = 4             # Gelado: todas as notas abaixo disso
CARRASCO = 4           # Carrasco: mais da metade das notas que deu são esta ou menos
CORACAO_MOLE = 8       # Coração mole: mais da metade das notas que deu são esta ou mais
UNANIMIDADE = 1.0      # desvio das notas de até 1 ponto (escala de 1 a 10)
POLEMICO = 2.5         # desvio de 2,5 pontos ou mais
MUDANCA = 3            # posições que precisa subir (🚀) ou cair (📉)
MAX_TAGS = 2           # quantas tags aparecem para cada um

# da mais rara por natureza para a mais comum: desempata quando duas tags são igualmente raras na semana
ORDEM = ["Craque da galera", "Segue no topo", "Pior do racha", "Campeão da última semana", "Escapou por pouco",
         "Carrasco", "Coração mole", "Polêmico", "Em alta", "Em baixa", "Misterioso", "Quente", "Gelado",
         "Já levou 10", "Unanimidade", "Mediano", "Já votou"]


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


def _jeito_de_votar(dadas):
    """Carrasco ou Coração mole, pelas notas que a pessoa deu (basta mais da metade, não precisa ser todas)."""
    if len(dadas) < MIN_NOTAS:
        return None
    if sum(1 for n in dadas if n <= CARRASCO) * 2 > len(dadas):
        return _tag("🔪", "Carrasco", "lanterna")
    if sum(1 for n in dadas if n >= CORACAO_MOLE) * 2 > len(dadas):
        return _tag("🍬", "Coração mole", "doce")
    return None


def destaques(ranking, votaram, semanas, dadas=None):
    """As tags que aparecem (as mais raras) e o movimento de cada um do ranking.

    `ranking`: linhas de resultados() que têm nota, já na ordem; `votaram`: ids de quem votou esta semana;
    `semanas`: o histórico (da mais antiga para a mais nova); `dadas`: {id: notas que a pessoa deu}.
    Devolve {id: {"mostrar": [até MAX_TAGS], "tags": [todas, da mais rara para a mais comum], "mov": {...} | None}}.
    """
    dadas = dadas or {}
    ultima = semanas[-1] if semanas else None
    antes = {r["id"]: i for i, r in enumerate(ultima["ranking"], start=1)} if ultima else {}
    campeao = ultima["ranking"][0]["id"] if ultima and ultima["ranking"] else None
    total = len(ranking)

    saida = {}
    for posicao, linha in enumerate(ranking, start=1):
        # as faixas e o desvio olham as notas que contam na média (sem a maior e a menor de quem tem 10 ou mais)
        pid, notas, tags, mov = linha["id"], linha["contadas"], [], None
        if posicao == 1:
            tags.append(_tag("👑", "Segue no topo" if pid == campeao else "Craque da galera", "ouro"))
        elif pid == campeao:
            tags.append(_tag("🏅", "Campeão da última semana", "ouro"))
        if posicao == total and total > 3:
            tags.append(_tag("🏮", "Pior do racha", "lanterna"))
        if posicao == total - ZONA_REBAIXAMENTO and posicao > 3:
            tags.append(_tag("😮‍💨", "Escapou por pouco", "neutro"))
        if pid in antes:
            mov = _movimento(antes[pid], posicao)
            if antes[pid] - posicao >= MUDANCA:
                tags.append(_tag("🚀", "Em alta", "quente"))
            elif posicao - antes[pid] >= MUDANCA:
                tags.append(_tag("📉", "Em baixa", "frio"))
        jeito = _jeito_de_votar(dadas.get(pid, []))
        if jeito:
            tags.append(jeito)
        if linha["recebidas"] and max(linha["recebidas"]) >= 10:  # levou o 10, mesmo que ele tenha saído da média
            tags.append(_tag("💯", "Já levou 10", "ouro"))
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
        if outros_que_votaram >= MIN_NOTAS and linha["qtd"] * 2 < outros_que_votaram:
            tags.append(_tag("🕵️", "Misterioso", "neutro"))
        if pid in votaram:
            tags.append(_tag("🗳️", "Já votou", "suave"))
        saida[pid] = {"tags": tags, "mov": mov}

    # só as mais raras de cada um: as que menos gente tem nesta semana; empate pela ORDEM; "Já votou" por último
    quantos_tem = Counter(t["texto"] for e in saida.values() for t in e["tags"])

    def raridade(t):
        return (t["texto"] == "Já votou", quantos_tem[t["texto"]], ORDEM.index(t["texto"]))

    for e in saida.values():
        e["tags"].sort(key=raridade)
        outras = [t for t in e["tags"] if t["texto"] != "Já votou"]
        e["mostrar"] = outras[:MAX_TAGS] or e["tags"][:1]  # "Já votou" só para quem não tem outra
    return saida
