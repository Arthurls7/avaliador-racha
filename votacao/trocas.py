"""Pedidos de troca de linha.

Um pedido é de um destes tipos:
- "troca": o jogador troca de lugar com alguém de outra linha (as linhas continuam do mesmo tamanho);
- "mudar": o jogador vai para outra linha (a dele fica com um a menos e a outra com um a mais).

Ele só é aplicado quando o organizador aprova. Como as linhas podem ter mudado desde o pedido,
aplicar() confere tudo de novo e devolve None se o pedido não fizer mais sentido.
"""


def aplicar(linhas, pedido):
    """Devolve novas linhas (listas de ids) com o pedido aplicado, ou None se não dá mais para aplicar."""
    onde = {j: i for i, linha in enumerate(linhas) for j in linha}
    jogador = pedido["jogador"]
    if jogador not in onde:
        return None
    novas = [list(linha) for linha in linhas]
    origem = onde[jogador]

    if pedido["tipo"] == "troca":
        outro = pedido.get("com")
        if outro not in onde or onde[outro] == origem:
            return None
        destino = onde[outro]
        novas[origem][novas[origem].index(jogador)] = outro
        novas[destino][novas[destino].index(outro)] = jogador
    else:
        destino = (pedido.get("destino") or 0) - 1
        if not 0 <= destino < len(novas) or destino == origem:
            return None
        novas[origem].remove(jogador)
        novas[destino].append(jogador)
    return novas


def descrever(pedido, nomes, linhas=None):
    """Texto curto do pedido (ex.: "🔄 Trocar com Pedro (Linha 2)").

    Com `linhas`, diz em que linha o outro está hoje; sem, fica só o nome (bom para o histórico,
    em que a linha de hoje já pode ser outra por causa da própria troca).
    """
    onde = {j: i + 1 for i, linha in enumerate(linhas or []) for j in linha}
    if pedido["tipo"] == "troca":
        outro = pedido.get("com")
        linha = f" (Linha {onde[outro]})" if outro in onde else ""
        return f"🔄 Trocar com {nomes.get(outro, 'alguém que saiu')}{linha}"
    return f"➡️ Ir para a Linha {pedido.get('destino')}"


def comparar(antes, depois):
    """Uma linha (time) antes e depois da troca, lado a lado, para o organizador conferir.

    Devolve uma lista de pares (como está, como fica); cada lado é (id, situação) ou None, com
    situação "fica", "sai" ou "entra". Quem entra aparece na mesma altura de quem sai.
    """
    entram = [j for j in depois if j not in antes]
    pares = []
    for j in antes:
        if j in depois:
            pares.append(((j, "fica"), (j, "fica")))
        else:
            novo = entram.pop(0) if entram else None
            pares.append(((j, "sai"), (novo, "entra") if novo else None))
    pares += [(None, (j, "entra")) for j in entram]
    return pares
