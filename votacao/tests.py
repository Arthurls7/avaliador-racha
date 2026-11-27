import json
import random
import re
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from . import armazenamento as arm
from .destaques import destaques
from .notas import contadas, ler_nota, na_escala, resultados
from .sorteio import (GrupoNaoCabe, NaoDeuParaSeparar, RegrasEmConflito, agrupar, diferenca, forcas, sortear, tamanhos,
                      tamanhos_por_quantidade)
from .trocas import aplicar, comparar, descrever
from .views import _secoes_do_ranking

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class TrocasTest(SimpleTestCase):
    linhas = [["a", "b"], ["c", "d"], ["e"]]

    def test_trocar_de_lugar(self):
        self.assertEqual(aplicar(self.linhas, {"jogador": "a", "tipo": "troca", "com": "c"}),
                         [["c", "b"], ["a", "d"], ["e"]])

    def test_mudar_de_linha(self):
        self.assertEqual(aplicar(self.linhas, {"jogador": "a", "tipo": "mudar", "destino": 3}),
                         [["b"], ["c", "d"], ["e", "a"]])

    def test_pedido_que_nao_da_mais(self):
        for pedido in [
            {"jogador": "a", "tipo": "troca", "com": "b"},      # mesma linha
            {"jogador": "a", "tipo": "troca", "com": "zz"},     # não está nas linhas
            {"jogador": "zz", "tipo": "troca", "com": "c"},
            {"jogador": "a", "tipo": "mudar", "destino": 1},    # já está nela
            {"jogador": "a", "tipo": "mudar", "destino": 4},    # não existe
        ]:
            self.assertIsNone(aplicar(self.linhas, pedido), pedido)
        self.assertEqual(self.linhas, [["a", "b"], ["c", "d"], ["e"]])  # não mexe nas linhas originais

    def test_comparar_lado_a_lado(self):
        # troca: quem entra fica na altura de quem sai
        self.assertEqual(comparar(["a", "b"], ["c", "b"]),
                         [(("a", "sai"), ("c", "entra")), (("b", "fica"), ("b", "fica"))])
        # mudar de linha: a de origem perde um, a de destino ganha um no fim
        self.assertEqual(comparar(["a", "b"], ["b"]), [(("a", "sai"), None), (("b", "fica"), ("b", "fica"))])
        self.assertEqual(comparar(["c"], ["c", "a"]), [(("c", "fica"), ("c", "fica")), (None, ("a", "entra"))])

    def test_descrever(self):
        nomes = {"a": "Ana", "c": "Caio"}
        self.assertEqual(descrever({"tipo": "troca", "com": "c"}, nomes, self.linhas), "🔄 Trocar com Caio (Linha 2)")
        self.assertEqual(descrever({"tipo": "mudar", "destino": 3}, nomes, self.linhas), "➡️ Ir para a Linha 3")
        # no histórico vai sem a linha de hoje (depois da troca, o outro já mudou de linha)
        self.assertEqual(descrever({"tipo": "troca", "com": "c"}, nomes), "🔄 Trocar com Caio")


class SorteioTest(SimpleTestCase):
    def test_tamanhos(self):
        self.assertEqual(tamanhos(17, 5), [5, 5, 5, 2])
        self.assertEqual(tamanhos(10, 5), [5, 5])
        self.assertEqual(tamanhos(3, 5), [3])
        self.assertEqual(tamanhos(0, 5), [])

    def test_tamanhos_por_numero_de_linhas(self):
        self.assertEqual(tamanhos_por_quantidade(17, 3), [6, 6, 5])
        self.assertEqual(tamanhos_por_quantidade(10, 4), [3, 3, 2, 2])
        self.assertEqual(tamanhos_por_quantidade(3, 5), [1, 1, 1])  # mais linhas que gente: uma por pessoa
        self.assertEqual(tamanhos_por_quantidade(0, 3), [])

    def test_todo_mundo_em_uma_linha_so(self):
        ids = [f"j{i}" for i in range(17)]
        forca = {j: i % 5 for i, j in enumerate(ids)}
        for tams in [tamanhos(17, 5), tamanhos_por_quantidade(17, 3)]:
            linhas, _ = sortear(ids, forca, tams)
            self.assertEqual(sorted(sum(linhas, [])), sorted(ids))
            self.assertEqual([len(l) for l in linhas], tams)

    def test_limite_zero_e_o_mais_equilibrado(self):
        forca = {"a": 5, "b": 5, "c": 5, "d": 5, "e": 1, "f": 1, "g": 1, "h": 1}
        for _ in range(5):
            linhas, dif = sortear(list(forca), forca, [4, 4], diferenca_max=0)
            self.assertEqual([sum(forca[j] for j in l) / 4 for l in linhas], [3, 3])
            self.assertEqual(dif, 0)

    def test_limite_e_respeitado(self):
        aleatorio = random.Random(7)
        ids = [f"j{i}" for i in range(20)]
        forca = {j: round(aleatorio.uniform(0.5, 5), 2) for j in ids}
        for limite in [0.1, 0.3, 1.0]:
            for _ in range(10):
                linhas, dif = sortear(ids, forca, tamanhos(20, 5), diferenca_max=limite)
                self.assertLessEqual(dif, limite + 1e-9)
                self.assertAlmostEqual(dif, diferenca(linhas, forca))

    def test_limite_maior_da_mais_sorte(self):
        forca = {"a": 5, "b": 5, "c": 5, "d": 5, "e": 1, "f": 1, "g": 1, "h": 1}
        difs = [sortear(list(forca), forca, [4, 4], diferenca_max=5)[1] for _ in range(30)]
        self.assertTrue(any(d > 0 for d in difs))  # com limite folgado, nem sempre sai o equilíbrio perfeito

    def test_limite_impossivel_devolve_o_mais_equilibrado(self):
        linhas, dif = sortear(["forte", "fraco"], {"forte": 5, "fraco": 0}, [1, 1], diferenca_max=0.5)
        self.assertEqual(dif, 5)  # só há um jeito: quem chamou avisa que passou do limite

    def test_sortear_de_novo_muda(self):
        ids = [f"j{i}" for i in range(10)]
        sorteios = {tuple(sorted(tuple(sorted(l)) for l in sortear(ids, dict.fromkeys(ids, 3), [5, 5])[0]))
                    for _ in range(10)}
        self.assertGreater(len(sorteios), 1)

    def test_agrupar_duplas(self):
        # duplas encadeadas viram um grupo; quem não está na lista é ignorado
        self.assertEqual(agrupar(list("abcdefg"), [("a", "b"), ("b", "c"), ("d", "e"), ("x", "a")]),
                         [["a", "b", "c"], ["d", "e"]])
        self.assertEqual(agrupar(list("abc"), []), [])

    def test_quem_cai_junto_fica_na_mesma_linha(self):
        aleatorio = random.Random(5)
        ids = [f"j{i}" for i in range(17)]
        forca = {j: round(aleatorio.uniform(0, 5), 2) for j in ids}
        juntos = [("j0", "j1"), ("j1", "j2"), ("j7", "j8"), ("j12", "j16")]
        for tams in [tamanhos(17, 5), tamanhos_por_quantidade(17, 3)]:
            for equilibrar in [True, False]:
                for _ in range(15):
                    linhas, dif = sortear(ids, forca, tams, equilibrar, 0.3, juntos=juntos)
                    onde = {j: i for i, linha in enumerate(linhas) for j in linha}
                    self.assertTrue(all(onde[a] == onde[b] for a, b in juntos))
                    self.assertEqual([len(l) for l in linhas], tams)
                    self.assertEqual(sorted(onde), sorted(ids))

    def test_grupo_que_nao_cabe(self):
        with self.assertRaises(GrupoNaoCabe) as erro:
            sortear(list("abcdef"), dict.fromkeys("abcdef", 3), [2, 2, 2], juntos=[("a", "b"), ("b", "c")])
        self.assertEqual(sorted(erro.exception.grupo), ["a", "b", "c"])

    def test_quem_nao_cai_junto_fica_em_linhas_diferentes(self):
        aleatorio = random.Random(7)
        ids = [f"j{i}" for i in range(15)]
        forca = {j: round(aleatorio.uniform(1, 10), 2) for j in ids}
        juntos = [("j0", "j1"), ("j5", "j6")]
        separados = [("j0", "j2"), ("j1", "j3"), ("j2", "j3"), ("j5", "j9"), ("j10", "j11"), ("x", "j4")]
        for tams in [tamanhos(15, 5), tamanhos_por_quantidade(15, 3)]:
            for equilibrar in [True, False]:
                for _ in range(15):
                    linhas, dif = sortear(ids, forca, tams, equilibrar, 0.3, juntos=juntos, separados=separados)
                    onde = {j: i for i, linha in enumerate(linhas) for j in linha}
                    self.assertTrue(all(onde[a] == onde[b] for a, b in juntos), linhas)
                    self.assertTrue(all(onde[a] != onde[b] for a, b in separados if a in onde and b in onde), linhas)
                    self.assertEqual([len(l) for l in linhas], tams)
                    self.assertEqual(sorted(onde), sorted(ids))

    def test_regras_que_nao_dao(self):
        # juntos (por um grupo) e separados ao mesmo tempo; avisa isso antes de "o grupo não cabe"
        for tams in [[3, 3], [2, 2, 2]]:
            with self.assertRaises(RegrasEmConflito):
                sortear(list("abcdef"), dict.fromkeys("abcdef", 5), tams, juntos=[("a", "b"), ("b", "c")],
                        separados=[("c", "a")])
        # três que não podem cair juntos, mas só duas linhas
        with self.assertRaises(NaoDeuParaSeparar):
            sortear(list("abcd"), dict.fromkeys("abcd", 5), [2, 2], separados=[("a", "b"), ("b", "c"), ("a", "c")])

    def test_sorteio_puro(self):
        forca = {"a": 5, "b": 5, "c": 1, "d": 1}
        linhas, dif = sortear(list(forca), forca, [2, 2], equilibrar=False)
        self.assertEqual(sorted(sum(linhas, [])), ["a", "b", "c", "d"])
        self.assertAlmostEqual(dif, diferenca(linhas, forca))

    def test_quem_nao_tem_nota_conta_como_media_geral(self):
        self.assertEqual(forcas(["a", "b", "c"], {"a": 4, "b": 2, "c": None}), {"a": 4, "b": 2, "c": 3})


class NotasTest(SimpleTestCase):
    def test_nota_e_inteiro_de_1_a_10(self):
        self.assertEqual([ler_nota(v) for v in ["1", "7", "10"]], [1, 7, 10])
        for invalido in ["0", "11", "-1", "2.5", "", None, "abc"]:
            self.assertIsNone(ler_nota(invalido))

    def test_com_10_notas_ou_mais_sai_a_maior_e_a_menor(self):
        pessoas = [{"id": pid, "nome": pid} for pid in ["alvo"] + [f"v{i}" for i in range(12)]]

        def linha_do_alvo(notas):
            avaliacoes = {f"v{i}": {"escala": 10, "notas": {"alvo": n}} for i, n in enumerate(notas)}
            return next(l for l in resultados(pessoas, avaliacoes) if l["id"] == "alvo")

        # 9 notas: todas contam
        nove = [1, 6, 6, 6, 6, 6, 6, 6, 10]
        linha = linha_do_alvo(nove)
        self.assertAlmostEqual(linha["nota"], sum(nove) / 9)
        self.assertEqual((linha["qtd"], linha["descartadas"], linha["maior"]), (9, 0, 10))
        # 10 notas: o 10 e o 1 saem da média e do desempate, mas continuam nas recebidas
        dez = [1, 6, 6, 6, 6, 6, 6, 6, 6, 10]
        linha = linha_do_alvo(dez)
        self.assertEqual(linha["nota"], 6)
        self.assertEqual((linha["qtd"], linha["descartadas"], linha["maior"]), (10, 2, 6))
        self.assertEqual(sorted(linha["recebidas"]), sorted(dez))
        self.assertEqual(linha["contadas"], [6] * 8)
        # notas repetidas: sai só uma de cada ponta
        self.assertEqual(contadas([10, 10, 1, 1, 5, 5, 5, 5, 5, 5]), [1, 5, 5, 5, 5, 5, 5, 10])
        self.assertEqual(contadas([7, 3]), [7, 3])
        self.assertEqual(contadas([]), [])

    def test_avaliacao_antiga_de_0_a_5_vira_1_a_10(self):
        antiga, nova = {}, {"escala": 10}
        self.assertEqual([na_escala(v, antiga) for v in [0, 5, 2.5]], [1, 10, 5.5])
        self.assertAlmostEqual(na_escala(3, antiga), 6.4)
        self.assertEqual(na_escala(7, nova), 7)

    def test_resultado_e_media_das_notas_recebidas(self):
        pessoas = [{"id": i, "nome": i.upper()} for i in "abc"]
        avaliacoes = {
            "a": {"escala": 10, "notas": {"b": 5, "c": 1}},
            "b": {"escala": 10, "notas": {"a": 3, "c": 2}},
            # formato antigo (de 0 a 5) ainda conta, convertido: 5 -> 10 e 0,5 -> 1,9
            "c": {"modo": "ranking", "ranking": ["a", "b"], "notas": {"a": 5, "b": 0.5}},
            "saiu": {"escala": 10, "notas": {"a": 1}},  # não joga esta semana
        }
        linhas = {l["id"]: l for l in resultados(pessoas, avaliacoes)}
        self.assertEqual(linhas["a"]["nota"], 6.5)
        self.assertEqual(linhas["a"]["qtd"], 2)
        self.assertAlmostEqual(linhas["b"]["nota"], 3.45)
        self.assertEqual(linhas["c"]["nota"], 1.5)
        self.assertEqual([l["id"] for l in resultados(pessoas, avaliacoes)], ["a", "b", "c"])

    def test_desempate_maior_nota_e_depois_ordem_alfabetica(self):
        nomes = {"bia": "Bia", "alvaro": "Álvaro", "caio": "Caio", "duda": "Duda", "eva": "Eva", "juiz": "Juiz"}
        pessoas = [{"id": pid, "nome": nome} for pid, nome in nomes.items()]
        avaliacoes = {
            "juiz": {"notas": {"bia": 3, "alvaro": 3, "caio": 1, "duda": 4, "eva": 3}},
            "eva": {"notas": {"bia": 3, "alvaro": 3, "caio": 5, "duda": 3}},
        }
        # Duda: média 3,5            -> 1º pela média
        # Caio: média 3, maior nota 5 -> 2º pela maior nota
        # Álvaro, Bia e Eva: média 3, maior nota 3 -> empate total, ordem alfabética (acento não atrapalha)
        # Juiz: não recebeu nota      -> fim
        ordem = [l["nome"] for l in resultados(pessoas, avaliacoes)]
        self.assertEqual(ordem, ["Duda", "Caio", "Álvaro", "Bia", "Eva", "Juiz"])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SecoesDoRankingTest(SimpleTestCase):
    def test_zona_de_rebaixamento_sao_os_4_ultimos_sem_pegar_o_podio(self):
        for n in range(0, 21):
            ranking = list(range(1, n + 1))  # as posições
            s = _secoes_do_ranking(ranking)
            # todo mundo aparece uma vez só, na ordem
            self.assertEqual(s["podio"] + s["disputa"] + s["resto"] + s["rebaixamento"], ranking, n)
            self.assertEqual(s["rebaixamento"], ranking[3:][-4:], n)
            self.assertTrue(set(s["disputa"]) <= {4, 5, 6}, n)
        self.assertEqual(_secoes_do_ranking(list(range(1, 17)))["rebaixamento"], [13, 14, 15, 16])
        self.assertEqual(_secoes_do_ranking(list(range(1, 6))), {"podio": [1, 2, 3], "disputa": [], "resto": [],
                                                                   "rebaixamento": [4, 5]})


class FluxoTest(SimpleTestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)
        ajuste = override_settings(VOTACAO_DADOS_DIR=self.pasta / "dados", MEDIA_ROOT=self.pasta / "media")
        ajuste.enable()
        self.addCleanup(ajuste.disable)
        arm.adicionar_pessoas(["Davi", "Ana", "Caio", "Bruno"])
        arm.definir_admin("chefe", "segredo")
        cache.clear()  # contador de senhas erradas

    def json(self, arquivo):
        return json.loads((self.pasta / "dados" / arquivo).read_text(encoding="utf-8"))

    def entrar(self):
        self.client.post(reverse("votacao:painel_entrar"), {"usuario": "chefe", "senha": "segredo"})

    def senha(self, pessoa_id):
        return next(p["senha"] for p in arm.pessoas() if p["id"] == pessoa_id)

    def logar(self, pessoa_id):
        """Entra como o jogador, com a senha dele."""
        resposta = self.client.post(reverse("votacao:entrar", args=[pessoa_id]), {"senha": self.senha(pessoa_id)})
        self.assertRedirects(resposta, reverse("votacao:avaliar", args=[pessoa_id]), fetch_redirect_response=False)

    def joga(self):
        return {p["nome"]: p["joga"] for p in arm.pessoas()}

    # ---------- cadastro e lista da semana ----------

    def test_lista_de_nomes(self):
        texto = "1. Ana\n2 - Bruno \n\n3) caio\nCaio\n  Davi  Souza "
        self.assertEqual(arm.limpar_nomes(texto), ["Ana", "Bruno", "caio", "Davi Souza"])

    def test_cadastro_em_ordem_alfabetica(self):
        arm.adicionar_pessoas(["Álvaro"])
        self.assertEqual([p["nome"] for p in arm.pessoas()], ["Álvaro", "Ana", "Bruno", "Caio", "Davi"])

    def test_adicionar_marca_quem_existe_e_cadastra_quem_e_novo(self):
        arm.salvar_cadastro({"ana", "bruno", "caio", "davi"}, {}, set())  # ninguém joga
        novos, existentes, desativados = arm.adicionar_pessoas(["ana", "Eva"])
        self.assertEqual((novos, existentes, desativados), (1, 1, []))
        self.assertEqual(self.joga(), {"Ana": True, "Bruno": False, "Caio": False, "Davi": False, "Eva": True})

    def test_lista_completa_da_semana(self):
        arm.adicionar_pessoas(["Bruno", "Eva"], somente_estes=True)
        self.assertEqual(self.joga(), {"Ana": False, "Bruno": True, "Caio": False, "Davi": False, "Eva": True})

    def test_desativar_pessoa(self):
        # a Ana dá notas bem diferentes das do Bruno
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 2, "caio": 10, "davi": 6}})
        arm.salvar_avaliacao("bruno", {"escala": 10, "notas": {"ana": 9, "caio": 4, "davi": 6}})
        self.logar("ana")  # e está conectada neste celular
        self.entrar()
        resposta = self.client.post(reverse("votacao:painel_ativo"), {"id": "ana", "ativo": "0"}, follow=True)
        self.assertRedirects(resposta, reverse("votacao:painel_cadastro"))
        self.assertContains(resposta, "💤 Ana foi desativado(a)")
        self.assertContains(resposta, "Todo mundo que já jogou (4, 1 desativado)")
        self.assertContains(resposta, 'aria-label="Reativar Ana"')
        self.assertNotContains(resposta, 'name="joga" value="ana"')
        self.assertNotContains(resposta, 'aria-label="Desativar Ana"')
        self.assertContains(resposta, 'aria-label="Desativar Bruno"')
        ana = next(p for p in arm.pessoas() if p["id"] == "ana")
        self.assertEqual((ana["ativo"], ana["joga"]), (False, False))
        self.assertEqual(sorted(arm.avaliacoes()), ["ana", "bruno"])  # nada foi apagado

        # some do início e do ranking, e as notas que deu não contam (Caio fica com o 4 do Bruno, não com 7)
        inicio = self.client.get(reverse("votacao:inicio"))
        self.assertNotContains(inicio, reverse("votacao:avaliar", args=["ana"]))
        self.assertContains(inicio, "🗳️ 1 de 3 já votaram")
        ranking = self.client.get(reverse("votacao:ranking"))
        self.assertEqual(self.posicoes(ranking), [(1, "Davi"), (2, "Caio")])
        self.assertNotContains(ranking, "Ana")
        medias = {l["id"]: l["nota"] for l in resultados(arm.ativos(), arm.avaliacoes())}
        self.assertEqual(medias, {"bruno": None, "caio": 4, "davi": 6})
        # não vota mais (nem com a conexão que já tinha) e ninguém dá nota para ela
        self.assertRedirects(self.client.get(reverse("votacao:avaliar", args=["ana"])), reverse("votacao:inicio"))
        self.assertRedirects(self.client.get(reverse("votacao:entrar", args=["ana"])), reverse("votacao:inicio"))
        self.logar("bruno")
        self.assertNotContains(self.client.get(reverse("votacao:avaliar", args=["bruno"])), 'name="nota_ana"')

        # não joga: fica fora do sorteio, e nem a lista da semana nem o cadastro a colocam de volta
        self.client.post(reverse("votacao:painel_linhas_sortear"), {"divisao": "por_linha", "por_linha": "2"})
        self.assertEqual(sorted(sum(arm.linhas()["linhas"], [])), ["bruno", "caio", "davi"])
        resposta = self.client.post(reverse("votacao:painel_adicionar"), {"nomes": "Ana\nBruno"}, follow=True)
        self.assertContains(resposta, "Ficou fora da lista porque está desativado(a): Ana.")
        arm.salvar_cadastro({"ana"}, {}, {"ana"})
        self.assertIsNone(arm.buscar_jogador("ana"))

        # reativar: tudo volta, menos a lista da semana (o organizador marca "Joga" se for jogar)
        resposta = self.client.post(reverse("votacao:painel_ativo"), {"id": "ana", "ativo": "1"}, follow=True)
        self.assertContains(resposta, "✅ Ana foi reativado(a)")
        self.assertContains(resposta, 'name="joga" value="ana"')
        medias = {l["id"]: l["nota"] for l in resultados(arm.ativos(), arm.avaliacoes())}
        self.assertEqual(medias, {"ana": 9, "bruno": 2, "caio": 7, "davi": 6})
        self.assertContains(self.client.get(reverse("votacao:inicio")), reverse("votacao:avaliar", args=["ana"]))
        self.assertIsNone(arm.buscar_jogador("ana"))

    def test_quem_nao_joga_vota_e_aparece_mas_fica_fora_do_sorteio(self):
        arm.adicionar_pessoas(["Ana", "Bruno", "Caio"], somente_estes=True)  # Davi fora da lista da semana
        # aparece no início, e a barra de progresso conta todo mundo do cadastro
        resposta = self.client.get(reverse("votacao:inicio"))
        self.assertContains(resposta, reverse("votacao:avaliar", args=["davi"]))
        self.assertContains(resposta, "🗳️ 0 de 4 já votaram")
        # vota e é avaliado
        self.logar("davi")
        self.client.post(reverse("votacao:avaliar", args=["davi"]), {"nota_ana": "8", "nota_bruno": "6", "nota_caio": "4"})
        self.logar("ana")
        self.assertContains(self.client.get(reverse("votacao:avaliar", args=["ana"])), 'name="nota_davi"')
        self.client.post(reverse("votacao:avaliar", args=["ana"]), {"nota_bruno": "10", "nota_caio": "2", "nota_davi": "9"})
        self.assertEqual(sorted(arm.avaliacoes()), ["ana", "davi"])
        # as notas dele contam e ele entra no ranking (Bruno e Ana têm 8; Bruno recebeu um 10, então fica na frente)
        resposta = self.client.get(reverse("votacao:ranking"))
        self.assertEqual(self.posicoes(resposta), [(1, "Davi"), (2, "Bruno"), (3, "Ana"), (4, "Caio")])
        self.assertContains(resposta, "🗳️ 2 de 4 já votaram")
        # ...mas fica fora do sorteio das linhas, que usa as médias com as notas de todo mundo
        self.entrar()
        self.client.post(reverse("votacao:painel_linhas_sortear"), {"divisao": "por_linha", "por_linha": "2"})
        self.assertEqual(sorted(sum(arm.linhas()["linhas"], [])), ["ana", "bruno", "caio"])
        self.assertNotContains(self.client.get(reverse("votacao:painel_linhas")), "sem nota")  # a nota da Ana veio do Davi
        # o painel mostra todo mundo no resultado e avisa quem não joga
        painel = self.client.get(reverse("votacao:painel"))
        self.assertContains(painel, "2 de 4 pessoas já avaliaram")
        self.assertContains(painel, " · não joga esta semana", count=1)

    def test_renomear_mantem_id_e_avaliacoes(self):
        arm.salvar_avaliacao("ana", {"notas": {"bruno": 5}})
        repetidos = arm.salvar_cadastro({"ana", "bruno"}, {"ana": "Ana Paula", "bruno": "caio"}, {"ana", "bruno"})
        self.assertEqual(repetidos, ["caio"])
        nomes = {p["id"]: p["nome"] for p in arm.pessoas()}
        self.assertEqual(nomes["ana"], "Ana Paula")
        self.assertEqual(nomes["bruno"], "Bruno")
        self.assertIn("ana", arm.avaliacoes())
        # quem não estava no formulário não muda
        self.assertTrue(self.joga()["Davi"])

    def test_excluir_apaga_pessoa_foto_e_avaliacoes(self):
        arm.salvar_foto("bruno", SimpleUploadedFile("b.jpg", JPEG))
        foto = arm.pessoas()[1]["foto"]
        arm.salvar_avaliacao("bruno", {"notas": {"ana": 5}})
        arm.salvar_avaliacao("ana", {"modo": "ranking", "ranking": ["bruno", "caio"], "notas": {"bruno": 5, "caio": 0.5}})
        arm.salvar_avaliacao("caio", {"notas": {"ana": 3}, "nao_conhece": ["bruno", "davi"]})
        arm.salvar_sorteio([["bruno", "ana"], ["caio", "davi"]], 2, True)
        arm.excluir_pessoa("bruno")
        self.assertNotIn("Bruno", self.joga())
        self.assertNotIn("bruno", arm.avaliacoes())
        self.assertEqual(arm.avaliacoes()["ana"]["notas"], {"caio": 0.5})
        self.assertEqual(arm.avaliacoes()["caio"]["nao_conhece"], ["davi"])
        self.assertEqual(arm.linhas()["linhas"], [["ana"], ["caio", "davi"]])
        self.assertFalse((arm.pasta_fotos() / foto).exists())

    def test_dados_antigos_sem_joga_e_foto(self):
        (self.pasta / "dados" / "logins.json").write_text(
            json.dumps({"admin": None, "pessoas": [{"id": "ze", "nome": "Zé"}]}), encoding="utf-8")
        self.assertEqual(arm.jogadores(), [{"id": "ze", "nome": "Zé", "foto": None, "joga": True, "senha": None,
                                            "ativo": True}])

    # ---------- fotos ----------

    def test_foto(self):
        self.assertTrue(arm.salvar_foto("ana", SimpleUploadedFile("a.jpg", JPEG)))
        arquivo = arm.buscar_jogador("ana")["foto"]
        self.assertRegex(arquivo, r"^ana-\d+\.jpg$")
        resposta = self.client.get(reverse("votacao:foto", args=[arquivo]))
        self.assertEqual(resposta["Content-Type"], "image/jpeg")
        self.assertEqual(b"".join(resposta.streaming_content), JPEG)
        self.assertContains(self.client.get(reverse("votacao:inicio")), reverse("votacao:foto", args=[arquivo]))
        # trocar a foto apaga a antiga
        self.assertTrue(arm.salvar_foto("ana", SimpleUploadedFile("a.jpg", JPEG)))
        self.assertEqual([f.name for f in arm.pasta_fotos().iterdir()], [arm.buscar_jogador("ana")["foto"]])
        arm.remover_foto("ana")
        self.assertIsNone(arm.buscar_jogador("ana")["foto"])
        self.assertEqual(list(arm.pasta_fotos().iterdir()), [])

    def test_foto_invalida(self):
        self.assertFalse(arm.salvar_foto("ana", SimpleUploadedFile("a.jpg", b"<script>oi</script>")))
        self.assertFalse(arm.salvar_foto("ninguem", SimpleUploadedFile("a.jpg", JPEG)))
        self.assertEqual(list(arm.pasta_fotos().iterdir()), [])
        for arquivo in ["../logins.json", "ana.jpg", "ana-1.exe"]:
            self.assertEqual(self.client.get(f"/media/fotos/{arquivo}").status_code, 404)

    # ---------- avaliação ----------

    def test_inicio_lista_pessoas(self):
        resposta = self.client.get(reverse("votacao:inicio"))
        self.assertContains(resposta, "Bruno")
        self.assertContains(resposta, reverse("votacao:avaliar", args=["ana"]))

    def test_avaliar(self):
        url = reverse("votacao:avaliar", args=["ana"])
        self.logar("ana")
        pagina = self.client.get(url)
        self.assertNotContains(pagina, "nota_ana")  # não avalia a si mesmo
        self.assertContains(pagina, 'name="nota_bruno"', count=11)  # botões de 1 a 10 + "Não conheço"
        self.assertContains(pagina, "Dê uma nota de 1 a 10")
        self.assertNotContains(pagina, "Nota anterior")  # primeira vez: nada para mostrar
        resposta = self.client.post(url, {"nota_bruno": "1", "nota_caio": "10", "nota_davi": "7"})
        self.assertRedirects(resposta, reverse("votacao:inicio"))
        registro = self.json("avaliacoes.json")["avaliacoes"]["ana"]
        self.assertEqual((registro["escala"], registro["notas"]), (10, {"bruno": 1, "caio": 10, "davi": 7}))
        self.assertEqual(registro["nao_conhece"], [])
        # ao voltar, as notas vêm marcadas e a nota anterior aparece em cada cartão
        pagina = self.client.get(url)
        self.assertContains(pagina, 'name="nota_davi" value="7" required aria-label="7" checked')
        for nota in ["1", "10", "7"]:
            self.assertContains(pagina, f"Nota anterior: <b>{nota}</b>")

    def test_nao_conheco(self):
        url = reverse("votacao:avaliar", args=["ana"])
        self.logar("ana")
        resposta = self.client.post(url, {"nota_bruno": "nao-conheco", "nota_caio": "8", "nota_davi": "nao-conheco"})
        self.assertRedirects(resposta, reverse("votacao:inicio"))
        registro = self.json("avaliacoes.json")["avaliacoes"]["ana"]
        self.assertEqual(registro["notas"], {"caio": 8})
        self.assertEqual(registro["nao_conhece"], ["bruno", "davi"])
        # "Não conheço" fica fora da média
        arm.salvar_avaliacao("caio", {"escala": 10, "notas": {"bruno": 4, "davi": 2, "ana": 6}})
        linhas = {l["id"]: l for l in resultados(arm.jogadores(), arm.avaliacoes())}
        self.assertEqual((linhas["bruno"]["nota"], linhas["bruno"]["qtd"]), (4, 1))
        self.assertEqual((linhas["caio"]["nota"], linhas["caio"]["qtd"]), (8, 1))
        # ao voltar, "Não conheço" vem marcado
        pagina = self.client.get(url)
        self.assertContains(pagina, 'name="nota_bruno" value="nao-conheco" required checked')
        self.assertContains(pagina, "Nota anterior: <b>não conheço</b>", count=2)

    def test_avaliar_faltando_ou_fora_do_limite(self):
        url = reverse("votacao:avaliar", args=["ana"])
        self.logar("ana")
        resposta = self.client.post(url, {"nota_bruno": "11", "nota_caio": "4", "nota_davi": "0"})
        self.assertContains(resposta, "para: Bruno, Davi.")
        self.assertContains(resposta, 'name="nota_caio" value="4" required aria-label="4" checked')  # não perde o que marcou
        self.assertEqual(arm.avaliacoes(), {})

    def test_avaliacao_antiga_aparece_com_a_escala_antiga(self):
        # avaliação feita quando as notas eram de 0 a 5 (com notas quebradas do ranking antigo)
        arm.salvar_avaliacao("ana", {"modo": "ranking", "ranking": ["caio", "davi", "bruno"],
                                     "notas": {"caio": 5.0, "davi": 2.75, "bruno": 0.5}})
        self.logar("ana")
        pagina = self.client.get(reverse("votacao:avaliar", args=["ana"])).content.decode()
        # nada vem marcado (a escala era outra), mas a nota antiga aparece dizendo de quanto era
        self.assertEqual(pagina.count(" checked"), 0)
        self.assertIn("Nota anterior: <b>5 (de 0 a 5)</b>", pagina)
        self.assertIn("Nota anterior: <b>2,75 (de 0 a 5)</b>", pagina)
        self.assertIn("Nota anterior: <b>0,5 (de 0 a 5)</b>", pagina)
        self.assertIn("quando as notas eram de 0 a 5", pagina)
        self.assertNotIn("mude o que quiser", pagina)

    # ---------- senha de 4 números ----------

    def test_cada_um_tem_uma_senha_de_4_numeros(self):
        senhas = [p["senha"] for p in arm.pessoas()]
        self.assertTrue(all(re.fullmatch(r"\d{4}", s) for s in senhas))
        self.assertEqual(len(set(senhas)), len(senhas))  # uma diferente para cada um
        arm.adicionar_pessoas(["Eva"])
        self.assertRegex(self.senha("eva"), r"^\d{4}$")

    def test_quem_foi_cadastrado_antes_das_senhas_ganha_uma(self):
        admin = arm.ler(arm.LOGINS)["admin"]
        (self.pasta / "dados" / "logins.json").write_text(
            json.dumps({"admin": admin, "pessoas": [{"id": "ze", "nome": "Zé"}, {"id": "lia", "nome": "Lia"}]}),
            encoding="utf-8")
        self.assertContains(self.client.get(reverse("votacao:entrar", args=["ze"])), "ainda não foi criada")
        self.entrar()
        resposta = self.client.get(reverse("votacao:painel_cadastro"))
        self.assertContains(resposta, "Senhas criadas para 2 pessoa(s)")
        self.assertContains(resposta, f"Senha <b>{self.senha('ze')}</b>")
        self.assertEqual(arm.garantir_senhas(), 0)
        self.logar("ze")

    def test_sem_senha_nao_ve_nem_muda_as_notas(self):
        arm.salvar_avaliacao("ana", {"notas": {"bruno": 5, "caio": 4, "davi": 3}})
        url = reverse("votacao:avaliar", args=["ana"])
        tela_senha = reverse("votacao:entrar", args=["ana"])
        self.assertRedirects(self.client.get(url), tela_senha)
        self.assertRedirects(self.client.post(url, {"nota_bruno": "0", "nota_caio": "0", "nota_davi": "0"}), tela_senha)
        self.assertEqual(arm.avaliacoes()["ana"]["notas"], {"bruno": 5, "caio": 4, "davi": 3})
        pagina = self.client.get(tela_senha)
        self.assertContains(pagina, 'inputmode="numeric"')
        self.assertNotContains(pagina, "Nota anterior")
        self.assertNotContains(pagina, "nota_bruno")

    def test_senha_de_um_nao_abre_outro(self):
        self.logar("ana")
        self.assertRedirects(self.client.get(reverse("votacao:avaliar", args=["bruno"])),
                             reverse("votacao:entrar", args=["bruno"]))
        resposta = self.client.post(reverse("votacao:entrar", args=["bruno"]), {"senha": self.senha("ana")})
        self.assertContains(resposta, "Senha errada")

    def test_senha_errada_bloqueia_depois_de_5(self):
        url = reverse("votacao:entrar", args=["ana"])
        errada = next(f"{n:04d}" for n in range(10000) if f"{n:04d}" != self.senha("ana"))
        for restam in [4, 3, 2]:
            self.assertContains(self.client.post(url, {"senha": errada}), f"Mais {restam} tentativas")
        self.assertContains(self.client.post(url, {"senha": errada}), "Mais 1 tentativa antes")
        self.assertContains(self.client.post(url, {"senha": errada}), "Bloqueado por 10 minutos")
        # bloqueado: nem a senha certa entra
        self.assertContains(self.client.post(url, {"senha": self.senha("ana")}), "Muitas tentativas erradas")
        self.logar("bruno")  # os outros não são afetados
        cache.clear()  # passaram os 10 minutos
        self.logar("ana")

    def test_salvar_nao_desconecta_e_sair_desconecta(self):
        url = reverse("votacao:avaliar", args=["ana"])
        self.logar("ana")
        self.assertRedirects(self.client.get(reverse("votacao:entrar", args=["ana"])), url)  # já entrou
        resposta = self.client.post(url, {"nota_bruno": "1", "nota_caio": "2", "nota_davi": "3"}, follow=True)
        self.assertContains(resposta, "Você continua conectado")
        self.assertEqual(self.client.get(url).status_code, 200)  # sem pedir a senha de novo
        self.assertRedirects(self.client.post(reverse("votacao:sair_jogador")), reverse("votacao:inicio"))
        self.assertRedirects(self.client.get(url), reverse("votacao:entrar", args=["ana"]))

    def test_conexao_dura_14_dias_e_renova_a_cada_uso(self):
        self.logar("ana")
        quatorze_dias = 14 * 24 * 3600
        self.assertAlmostEqual(self.client.session["jogador"]["ate"], time.time() + quatorze_dias, delta=60)
        # faltando 1 hora, usar renova por mais 14 dias
        sessao = self.client.session
        sessao["jogador"]["ate"] = time.time() + 3600
        sessao.save()
        self.assertEqual(self.client.get(reverse("votacao:avaliar", args=["ana"])).status_code, 200)
        self.assertAlmostEqual(self.client.session["jogador"]["ate"], time.time() + quatorze_dias, delta=60)
        # vencida: senha de novo
        sessao = self.client.session
        sessao["jogador"]["ate"] = 0
        sessao.save()
        self.assertRedirects(self.client.get(reverse("votacao:avaliar", args=["ana"])),
                             reverse("votacao:entrar", args=["ana"]))

    def test_inicio_mostra_quem_esta_conectado(self):
        self.assertNotContains(self.client.get(reverse("votacao:inicio")), "Conectado como")
        self.logar("ana")
        pagina = self.client.get(reverse("votacao:inicio"))
        self.assertContains(pagina, "Conectado como <strong>Ana</strong>")
        self.assertContains(pagina, '<span class="etiqueta-voce">você</span>', count=1)
        self.assertContains(pagina, f'action="{reverse("votacao:sair_jogador")}"')

    def test_organizador_troca_a_senha(self):
        self.logar("ana")
        antiga = self.senha("ana")
        self.entrar()
        resposta = self.client.post(reverse("votacao:painel_nova_senha"), {"id": "ana"}, follow=True)
        self.assertRedirects(resposta, reverse("votacao:painel_cadastro"))
        nova = self.senha("ana")
        self.assertNotEqual(nova, antiga)
        self.assertContains(resposta, f"Nova senha de Ana: {nova}")
        # quem entrou com a senha antiga é deslogado, e a antiga não funciona mais
        self.assertRedirects(self.client.get(reverse("votacao:avaliar", args=["ana"])),
                             reverse("votacao:entrar", args=["ana"]))
        self.assertContains(self.client.post(reverse("votacao:entrar", args=["ana"]), {"senha": antiga}), "Senha errada")
        self.logar("ana")

    # ---------- abas públicas: linhas e ranking ----------

    def test_abas_publicas(self):
        for nome in ["inicio", "linhas", "ranking"]:
            resposta = self.client.get(reverse(f"votacao:{nome}"))
            self.assertContains(resposta, f'href="{reverse(f"votacao:{nome}")}" class="ativa" aria-current="page"')
            for outra in ["inicio", "linhas", "ranking"]:
                self.assertContains(resposta, f'href="{reverse(f"votacao:{outra}")}"')

    def test_cabecalho_racha_do_doug(self):
        for url in [reverse("votacao:inicio"), reverse("votacao:entrar", args=["ana"]), reverse("votacao:painel_entrar")]:
            resposta = self.client.get(url)
            self.assertContains(resposta, "Racha do Doug</span>")
            self.assertContains(resposta, "· Racha do Doug</title>")
            self.assertContains(resposta, 'aria-label="Área do organizador"')
        # nas telas de senha e de avaliar, o menu marca "Avaliar"
        avaliar_ativo = f'href="{reverse("votacao:inicio")}" class="ativa" aria-current="page"'
        self.assertContains(self.client.get(reverse("votacao:entrar", args=["ana"])), avaliar_ativo)
        self.logar("ana")
        self.assertContains(self.client.get(reverse("votacao:avaliar", args=["ana"])), avaliar_ativo)
        self.entrar()
        for nome in ["painel", "painel_linhas", "painel_cadastro"]:
            resposta = self.client.get(reverse(f"votacao:{nome}"))
            self.assertContains(resposta, "Racha do Doug<small>Organizador</small>")
            self.assertContains(resposta, f'href="{reverse(f"votacao:{nome}")}" class="ativa" aria-current="page"')
            self.assertContains(resposta, 'aria-label="Ver o site"')
            self.assertContains(resposta, f'action="{reverse("votacao:painel_sair")}"')

    def posicoes(self, resposta):
        """[(posição, nome)] do ranking público, na ordem das posições."""
        achados = re.findall(r'data-posicao="(\d+)".*?<strong class="nome">([^<]+)</strong>',
                             resposta.content.decode(), re.S)
        return sorted((int(p), nome) for p, nome in achados)

    def sem_legenda(self, resposta):
        """A página do ranking até a legenda das tags (que tem o nome de todas as tags)."""
        return resposta.content.decode().split('class="legenda-tags"')[0]

    def test_ranking_publico_sem_media(self):
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 10, "caio": 3, "davi": 3}})
        arm.salvar_avaliacao("bruno", {"escala": 10, "notas": {"ana": 5, "caio": 7, "davi": 7}})
        # médias: Bruno 10; Ana, Caio e Davi 5. Desempate: Caio e Davi receberam um 7 (Ana no máximo 5);
        # entre Caio e Davi, ordem alfabética.
        resposta = self.client.get(reverse("votacao:ranking"))
        pagina = resposta.content.decode()
        self.assertEqual(self.posicoes(resposta), [(1, "Bruno"), (2, "Caio"), (3, "Davi"), (4, "Ana")])
        # o pódio vem na ordem de um pódio de verdade: 2º, 1º, 3º; depois, a briga pelo pódio
        self.assertEqual(re.findall(r'class="degrau p(\d)"', pagina), ["2", "1", "3"])
        self.assertContains(resposta, "👑</span>", count=1)  # a coroa em cima do 1º
        # com 4 pessoas, o 4º é o último: cai direto na zona de rebaixamento
        self.assertContains(resposta, "🔻 Zona de rebaixamento")
        self.assertContains(resposta, "O último da tabela. Ainda dá tempo de escapar!")
        self.assertEqual(re.findall(r'data-posicao="(\d+)"', pagina.split("Zona de rebaixamento")[1]), ["4"])
        self.assertNotContains(resposta, "Na briga pelo pódio")
        self.assertNotContains(resposta, "Só o básico")
        for medalha in ["🥇", "🥈", "🥉"]:
            self.assertEqual(pagina.count(medalha), 1)  # sem posições repetidas
        self.assertContains(resposta, "4º", count=1)
        for media in ["10,00", "5,00", "10,0", "5,0"]:
            self.assertNotIn(media, pagina)
        self.assertContains(resposta, "🗳️ 2 de 4 já votaram")
        # tags
        ranking = self.sem_legenda(resposta)
        self.assertEqual(ranking.count("👑 Craque da galera"), 1)
        # cada um mostra só a tag mais rara: a Ana (última) também é Carrasco e já votou, mas aparece "Pior do racha"
        self.assertEqual(ranking.count("🏮 Pior do racha"), 1)
        self.assertNotIn("Carrasco", ranking)
        self.assertNotIn("Já votou", ranking)
        self.assertEqual(ranking.count('class="tag '), 2)  # Bruno e Ana; Caio e Davi não têm tag
        self.assertNotIn("Estreante", resposta.content.decode())  # essa tag não existe mais
        self.assertNotIn('class="mov', ranking)  # nem setas de sobe/desce
        self.assertContains(resposta, "O que significam as tags?")

    def test_ranking_publico_secoes(self):
        arm.adicionar_pessoas(["Eva", "Fabio", "Gil", "Hugo", "Iris", "Juca", "Kiko", "Lia"])  # 12 jogadores
        ids = [p["id"] for p in arm.jogadores()]
        for i, quem in enumerate(ids):  # cada um recebe a mesma nota de todo mundo: Ana 1; Bruno 1,75; ...; Lia 9,25
            # ...mas de Gil em diante ninguém conhece a Ana (ela fica com 5 notas de 11: Misteriosa)
            arm.salvar_avaliacao(quem, {"escala": 10, "notas": {o: 1 + ids.index(o) * 0.75 for o in ids
                                                                if o != quem and not (o == "ana" and i >= 6)}})
        resposta = self.client.get(reverse("votacao:ranking"))
        self.assertEqual([p for p, _ in self.posicoes(resposta)], list(range(1, 13)))
        pagina = resposta.content.decode()

        def secao(titulo):
            return re.findall(r'data-posicao="(\d+)"', pagina.split(titulo)[1].split("</section>")[0])

        self.assertEqual(secao("🔥 Na briga pelo pódio"), ["4", "5", "6"])
        self.assertEqual(secao("⚽ Só o básico"), ["7", "8"])
        self.assertEqual(secao("🔻 Zona de rebaixamento"), ["9", "10", "11", "12"])
        self.assertContains(resposta, "Os 4 últimos da tabela. Ainda dá tempo de escapar!")
        self.assertEqual(self.posicoes(resposta)[0], (1, "Lia"))
        self.assertEqual(self.posicoes(resposta)[-1], (12, "Ana"))
        self.assertContains(resposta, "🎉 Todo mundo já votou!")
        ranking = self.sem_legenda(resposta)
        self.assertNotContains(resposta, "Todo mundo conhece")  # essa tag não existe mais
        # cada um mostra só a tag mais rara que tem. Todo mundo é Unanimidade e já votou: essas não aparecem.
        # Lia (1ª) é a craque; Kiko e Juca, quentes (3 têm); Iris a Fabio, medianos (5 têm); Eva (8ª) escapou por
        # pouco da zona; Davi, Caio e Bruno, gelados (4 têm); a Ana é a pior do racha (e também Misteriosa)
        self.assertEqual(ranking.count('class="tag '), 12)  # uma por pessoa
        self.assertEqual(ranking.count("👑 Craque da galera"), 1)
        self.assertEqual(ranking.count("🔥 Quente"), 2)
        self.assertEqual(ranking.count("😐 Mediano"), 4)
        self.assertEqual(ranking.count("😮‍💨 Escapou por pouco"), 1)
        self.assertEqual(ranking.count("🧊 Gelado"), 3)
        self.assertEqual(ranking.count("🏮 Pior do racha"), 1)
        for comum in ["Unanimidade", "Já votou", "Misterioso"]:
            self.assertNotIn(comum, ranking)
        self.assertIn("😮‍💨 Escapou por pouco", ranking.split("Só o básico")[1].split("</section>")[0])
        self.assertIn("🏮 Pior do racha", ranking.split("Zona de rebaixamento")[1])

    def test_ranking_publico_quem_nao_tem_nota(self):
        arm.salvar_avaliacao("ana", {"notas": {"bruno": 5}, "nao_conhece": ["caio", "davi"]})
        resposta = self.client.get(reverse("votacao:ranking"))
        self.assertContains(resposta, "🌱 Esperando notas")
        self.assertEqual(self.posicoes(resposta), [(1, "Bruno")])
        self.assertContains(resposta, "🥇", count=1)
        # sem nenhuma nota: um aviso no lugar do pódio
        arm.zerar_avaliacoes()
        resposta = self.client.get(reverse("votacao:ranking"))
        self.assertContains(resposta, "Ninguém recebeu nota ainda")
        self.assertNotContains(resposta, "Esperando notas")

    def test_ranking_compara_com_a_semana_fechada(self):
        self.entrar()
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 9, "caio": 6, "davi": 3}})
        self.client.post(reverse("votacao:painel_fechar_semana"), {"zerar": "on"})
        # semana passada: Bruno, Caio, Davi (Ana sem nota). Agora Davi passa todo mundo e chega Eva.
        arm.adicionar_pessoas(["Eva"])
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 5, "caio": 4, "davi": 10, "eva": 2}})
        resposta = self.client.get(reverse("votacao:ranking"))
        self.assertEqual(self.posicoes(resposta), [(1, "Davi"), (2, "Bruno"), (3, "Caio"), (4, "Eva")])
        self.assertContains(resposta, 'title="Subiu 2 desde a última semana">▲2</span>')
        self.assertContains(resposta, 'title="Caiu 1 desde a última semana">▼1</span>', count=2)
        ranking = self.sem_legenda(resposta)
        self.assertEqual(ranking.count("🏅 Campeão da última semana"), 1)  # Bruno
        self.assertEqual(ranking.count("🏮 Pior do racha"), 1)  # a Eva, a última
        self.assertContains(resposta, "📚 Pódios anteriores")
        self.assertContains(resposta, "🥇 Bruno · 🥈 Caio · 🥉 Davi")

    def test_destaques(self):
        def linha(pid, recebidas):
            return {"id": pid, "qtd": len(recebidas), "recebidas": recebidas, "contadas": recebidas}

        def todas(extras):
            return {pid: sorted(t["texto"] for t in e["tags"]) for pid, e in extras.items()}

        ranking = [linha("a", [9, 9, 10]), linha("b", [10, 2, 5, 9]), linha("c", [5, 6, 7]), linha("d", [3]),
                   linha("e", [1, 2, 3])]
        semana = {"ranking": [{"id": "e"}, {"id": "c"}, {"id": "x"}, {"id": "a"}, {"id": "b"}]}
        dadas = {"a": [2, 3, 9, 1], "b": [8, 9, 10, 4], "c": [4, 5, 8, 9], "f": [1, 1, 1]}
        extras = destaques(ranking, {"a", "b", "c", "f"}, [semana], dadas)
        self.assertEqual(todas(extras), {
            "a": sorted(["Craque da galera", "Em alta", "Carrasco", "Já levou 10", "Quente", "Unanimidade", "Já votou"]),
            "b": sorted(["Em alta", "Coração mole", "Já levou 10", "Polêmico", "Já votou"]),
            "c": sorted(["Mediano", "Unanimidade", "Já votou"]),  # metade alta, metade baixa: nem um nem outro
            "d": ["Misterioso"],  # nota de 1 dos 4 que votaram
            "e": sorted(["Campeão da última semana", "Em baixa", "Pior do racha", "Gelado", "Unanimidade"]),
        })
        # só aparece a mais rara de cada um (a que menos gente tem; empate pela ordem fixa)
        self.assertEqual({pid: e["tag"]["texto"] for pid, e in extras.items()}, {
            "a": "Craque da galera",  # Craque, Carrasco e Quente: só ele tem, e Craque vem antes na ordem
            "b": "Coração mole",      # só ele é Coração mole (Em alta e Já levou 10: dois têm)
            "c": "Mediano",           # só ele é Mediano (Unanimidade: três têm; Já votou fica por último)
            "d": "Misterioso",
            "e": "Pior do racha",     # empata com Campeão, Em baixa e Gelado (só ele tem): Pior vem antes na ordem
        })
        self.assertEqual({pid: (e["mov"] or {}).get("texto") for pid, e in extras.items()},
                         {"a": "▲3", "b": "▲3", "c": "▼1", "d": None, "e": "▼4"})
        # "Já votou" só aparece para quem não tem outra tag
        so_votou = destaques([linha("a", [5]), linha("b", [6])], {"a", "b"}, [])
        self.assertEqual(so_votou["b"]["tag"]["texto"], "Já votou")
        self.assertEqual(so_votou["a"]["tag"]["texto"], "Craque da galera")
        # sem histórico: sem setas; o 1º continua coroado e o último continua o pior do racha
        extras = destaques(ranking, set(), [])
        self.assertEqual(extras["a"]["tag"]["texto"], "Craque da galera")
        self.assertTrue(all(e["mov"] is None for e in extras.values()))
        self.assertEqual(extras["e"]["tag"]["texto"], "Pior do racha")
        # com 3 ou menos, o último está no pódio: não ganha "Pior do racha"
        self.assertNotIn("Pior do racha", str(destaques(ranking[:3], set(), [])))
        # Escapou por pouco: o primeiro acima dos 4 da zona de rebaixamento, nunca no pódio
        posicoes = [linha(f"p{i}", [5]) for i in range(1, 13)]
        escapou = [pid for pid, e in destaques(posicoes, set(), []).items()
                   if "Escapou por pouco" in str(e["tags"])]
        self.assertEqual(escapou, ["p8"])
        self.assertNotIn("Escapou por pouco", str(destaques(posicoes[:7], set(), [])))
        # com 10 notas ou mais, as faixas olham só as que contam; "Já levou 10" vale mesmo se o 10 saiu da média
        dez = {"id": "z", "qtd": 10, "recebidas": [10] + [5] * 8 + [1], "contadas": [5] * 8}
        tags = [t["texto"] for t in destaques([dez], set(), [])["z"]["tags"]]
        self.assertIn("Mediano", tags)
        self.assertIn("Unanimidade", tags)
        self.assertIn("Já levou 10", tags)
        # notas espalhadas entre as faixas: nem Quente, nem Mediano, nem Gelado
        tags = str(destaques([linha("a", [3, 5, 8])], set(), []))
        for faixa in ["Quente", "Mediano", "Gelado"]:
            self.assertNotIn(faixa, tags)

    def test_ranking_carrasco_e_coracao_mole(self):
        arm.adicionar_pessoas(["Eva"])
        arm.salvar_avaliacao("caio", {"escala": 10, "notas": {"ana": 6, "bruno": 6, "davi": 9, "eva": 1}})
        # a Ana dá quase tudo baixo (3 de 4 notas são 4 ou menos) e o Bruno, quase tudo alto (3 de 4 são 8 ou mais)
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 3, "caio": 2, "davi": 4, "eva": 9}})
        arm.salvar_avaliacao("bruno", {"escala": 10, "notas": {"ana": 8, "caio": 9, "davi": 10, "eva": 1}})
        resposta = self.client.get(reverse("votacao:ranking"))
        # Davi 7,67; Ana 7; Caio 5,5; Bruno 4,5; Eva 3,67
        self.assertEqual(self.posicoes(resposta), [(1, "Davi"), (2, "Ana"), (3, "Caio"), (4, "Bruno"), (5, "Eva")])
        pagina = self.sem_legenda(resposta)
        ana = re.search(r'data-posicao="2">.*?<div class="base">', pagina, re.S).group(0)
        bruno = re.search(r'<li data-posicao="4">.*?</li>', pagina, re.S).group(0)
        self.assertIn("🔪 Carrasco", ana)
        self.assertIn("🍬 Coração mole", bruno)
        self.assertEqual((pagina.count("Carrasco"), pagina.count("Coração mole")), (1, 1))

    def test_sorteio_por_numero_de_linhas_e_limite(self):
        self.entrar()
        arm.adicionar_pessoas(["Eva"])  # 5 jogadores
        sortear = reverse("votacao:painel_linhas_sortear")
        resposta = self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "2", "por_linha": "4",
                                              "equilibrar": "on", "diferenca_max": "0,5"}, follow=True)
        dados = arm.linhas()
        self.assertEqual([len(l) for l in dados["linhas"]], [3, 2])
        self.assertEqual((dados["divisao"], dados["num_linhas"], dados["diferenca_max"]), ("num_linhas", 2, 0.5))
        self.assertContains(resposta, "2 linha(s) sorteada(s) (3, 2 jogadores)")
        self.assertContains(resposta, "(limite 0,50)")
        # o painel lembra as escolhas
        self.assertContains(resposta, 'name="divisao" value="num_linhas" checked')
        self.assertContains(resposta, 'name="diferenca_max" id="diferenca_max" min="0" max="4" step="0.1" value="0.5"')
        self.assertContains(resposta, "Diferença entre a linha mais forte e a mais fraca agora")
        # mais linhas que gente: uma pessoa por linha
        self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "50"})
        self.assertEqual([len(l) for l in arm.linhas()["linhas"]], [1, 1, 1, 1, 1])
        # jogadores por linha continua funcionando
        self.client.post(sortear, {"divisao": "por_linha", "por_linha": "2"})
        self.assertEqual([len(l) for l in arm.linhas()["linhas"]], [2, 2, 1])

    def regra(self, regra, a, b, tirar=False):
        dados = {"regra": regra, "a": a, "b": b, **({"acao": "tirar"} if tirar else {})}
        return self.client.post(reverse("votacao:painel_linhas_regra"), dados, follow=True)

    def test_quem_cai_junto_no_painel(self):
        self.entrar()
        juntar = lambda a, b: self.regra("juntos", a, b)  # noqa: E731
        sortear = reverse("votacao:painel_linhas_sortear")
        resposta = juntar("ana", "bruno")
        self.assertRedirects(resposta, reverse("votacao:painel_linhas") + "#regras")
        self.assertContains(resposta, "🤝 Ana e Bruno vão cair juntos")
        self.assertEqual(arm.linhas()["juntos"], [["ana", "bruno"]])
        # repetida (em qualquer ordem) ou a mesma pessoa duas vezes não entra
        self.assertContains(juntar("bruno", "ana"), "Essa regra já existe")
        self.assertContains(juntar("ana", "ana"), "duas pessoas diferentes")
        self.assertEqual(len(arm.linhas()["juntos"]), 1)

        # o sorteio sempre põe os dois juntos (e continua valendo nos próximos sorteios)
        for _ in range(10):
            self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "2", "equilibrar": "on"})
            linhas = arm.linhas()["linhas"]
            self.assertTrue(any({"ana", "bruno"} <= set(linha) for linha in linhas), linhas)
        pagina = self.client.get(reverse("votacao:painel_linhas"))
        self.assertContains(pagina, 'title="Cai junto com alguém"', count=2)
        resposta = self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "2"}, follow=True)
        self.assertContains(resposta, "respeitando 1 regra(s)")

        # duplas encadeadas viram um grupo; se não couber na linha, o sorteio não acontece
        juntar("bruno", "caio")
        self.assertContains(self.client.get(reverse("votacao:painel_linhas")),
                            "Juntando as duplas, Ana, Bruno e Caio vão cair na mesma linha")
        antes = arm.linhas()["linhas"]
        resposta = self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "2"}, follow=True)
        self.assertContains(resposta, "Não deu para sortear: Ana, Bruno e Caio têm que cair juntos (3 pessoas)")
        self.assertContains(resposta, "tire alguma regra de &quot;cai junto&quot;")
        self.assertEqual(arm.linhas()["linhas"], antes)

        # tirar a dupla; quem sai da semana não conta, e quem é excluído some das duplas
        self.assertContains(self.regra("juntos", "caio", "bruno", tirar=True), "Regra tirada.")
        self.assertEqual(arm.linhas()["juntos"], [["ana", "bruno"]])
        juntar("caio", "davi")
        arm.salvar_cadastro({"davi"}, {}, set())  # Davi fora desta semana
        self.assertContains(self.client.get(reverse("votacao:painel_linhas")), "não joga esta semana: não conta")
        self.client.post(sortear, {"divisao": "por_linha", "por_linha": "2"})
        self.assertEqual(sorted(sum(arm.linhas()["linhas"], [])), ["ana", "bruno", "caio"])
        arm.excluir_pessoa("bruno")
        self.assertEqual(arm.linhas()["juntos"], [["caio", "davi"]])

    def test_quem_nao_cai_junto_no_painel(self):
        self.entrar()
        sortear = reverse("votacao:painel_linhas_sortear")
        resposta = self.regra("separados", "ana", "bruno")
        self.assertContains(resposta, "🙅 Ana e Bruno vão cair em linhas diferentes")
        self.assertEqual(arm.linhas()["separados"], [["ana", "bruno"]])
        pagina = self.client.get(reverse("votacao:painel_linhas"))
        self.assertContains(pagina, 'title="Não cai junto"')
        self.assertContains(pagina, '<option value="separados">🙅 não cai junto com</option>')

        # o sorteio nunca põe os dois na mesma linha, com ou sem equilíbrio
        for equilibrar in ["on", ""] * 6:
            resposta = self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "2", "equilibrar": equilibrar},
                                        follow=True)
            self.assertFalse(any({"ana", "bruno"} <= set(linha) for linha in arm.linhas()["linhas"]))
        self.assertContains(resposta, "respeitando 1 regra(s)")

        # a mesma dupla não pode ser "cai junto" e "não cai junto"
        resposta = self.regra("juntos", "bruno", "ana")
        self.assertContains(resposta, "Ana já estão em &quot;não cai junto&quot;. Tire aquela regra primeiro.")
        self.assertEqual(arm.linhas()["juntos"], [])
        # ...nem por um grupo: Ana+Caio e Caio+Bruno juntariam Ana e Bruno
        self.regra("juntos", "ana", "caio")
        self.regra("juntos", "caio", "bruno")
        antes = arm.linhas()["linhas"]
        resposta = self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "2"}, follow=True)
        self.assertContains(resposta, "têm que cair juntos (pelas duplas de &quot;cai junto&quot;) e separados")
        self.assertEqual(arm.linhas()["linhas"], antes)
        self.regra("juntos", "ana", "caio", tirar=True)
        self.regra("juntos", "caio", "bruno", tirar=True)

        # três que não podem se encontrar, mas só duas linhas
        self.regra("separados", "bruno", "caio")
        self.regra("separados", "ana", "caio")
        resposta = self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "2"}, follow=True)
        self.assertContains(resposta, "não há linhas suficientes para separar todo mundo")
        self.client.post(sortear, {"divisao": "num_linhas", "num_linhas": "3"})
        self.assertEqual(len(arm.linhas()["linhas"]), 3)

        # tirar e excluir
        self.regra("separados", "caio", "ana", tirar=True)
        self.assertEqual(arm.linhas()["separados"], [["ana", "bruno"], ["bruno", "caio"]])
        arm.excluir_pessoa("bruno")
        self.assertEqual(arm.linhas()["separados"], [])
        # regra inválida não faz nada
        self.regra("inimigos", "ana", "caio")
        self.assertEqual((arm.linhas()["juntos"], arm.linhas()["separados"]), ([], []))

    def test_aviso_quando_o_limite_nao_cabe(self):
        self.entrar()
        arm.adicionar_pessoas(["Ana", "Bruno"], somente_estes=True)
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 1}})
        arm.salvar_avaliacao("bruno", {"escala": 10, "notas": {"ana": 10}})
        resposta = self.client.post(reverse("votacao:painel_linhas_sortear"),
                                    {"divisao": "num_linhas", "num_linhas": "2", "equilibrar": "on",
                                     "diferenca_max": "0.5"}, follow=True)
        self.assertContains(resposta, "Não deu para ficar dentro do limite de 0,50")
        self.assertContains(resposta, "o mais equilibrado que deu foi 9,00")

    def test_linhas(self):
        self.entrar()
        publica = reverse("votacao:linhas")
        painel = reverse("votacao:painel_linhas")
        ajustar = reverse("votacao:painel_linhas_ajustar")
        self.assertContains(self.client.get(publica), "ainda não foram sorteadas")

        self.client.post(reverse("votacao:painel_linhas_sortear"), {"por_linha": "2", "equilibrar": "on"})
        dados = arm.linhas()
        self.assertEqual(sorted(len(l) for l in dados["linhas"]), [2, 2])
        self.assertEqual((dados["publicado"], dados["por_linha"], dados["equilibrar"]), (False, 2, True))
        self.assertContains(self.client.get(publica), "ainda não foram sorteadas")  # só depois de publicar
        self.assertContains(self.client.get(painel), "Publicar linhas")

        self.client.post(reverse("votacao:painel_linhas_publicar"))
        pagina = self.client.get(publica)
        for texto in ["Linha 1", "Linha 2", "Ana", "Bruno", "Caio", "Davi"]:
            self.assertContains(pagina, texto)

        # despublicar esconde de novo, sem perder o sorteio
        antes = arm.linhas()["linhas"]
        self.assertContains(self.client.get(painel), "Despublicar")
        resposta = self.client.post(reverse("votacao:painel_linhas_despublicar"), follow=True)
        self.assertRedirects(resposta, painel)
        self.assertContains(resposta, "Linhas escondidas")
        self.assertContains(resposta, "Publicar linhas")
        self.assertEqual((arm.linhas()["publicado"], arm.linhas()["linhas"]), (False, antes))
        self.assertContains(self.client.get(publica), "ainda não foram sorteadas")
        self.client.post(reverse("votacao:painel_linhas_publicar"))
        self.assertContains(self.client.get(publica), "Linha 2")

        # ajuste manual: muda alguém de linha (continua publicado)
        quem = arm.linhas()["linhas"][0][0]
        resposta = self.client.post(ajustar, {f"linha_{quem}": "2"})
        self.assertRedirects(resposta, painel + "#linha-2", fetch_redirect_response=False)
        linhas = arm.linhas()["linhas"]
        self.assertEqual((len(linhas[0]), len(linhas[1])), (1, 3))
        self.assertIn(quem, linhas[1])
        self.assertTrue(arm.linhas()["publicado"])
        # tira das linhas
        self.assertRedirects(self.client.post(ajustar, {f"linha_{quem}": "0"}), painel + "#fora",
                             fetch_redirect_response=False)
        self.assertNotIn(quem, sum(arm.linhas()["linhas"], []))
        self.assertContains(self.client.get(painel), "Fora das linhas (1)")
        # e manda para uma linha nova
        self.client.post(ajustar, {f"linha_{quem}": "3"})
        self.assertEqual(arm.linhas()["linhas"][2], [quem])

        # quem entra na lista depois do sorteio aparece em "Fora das linhas"
        arm.adicionar_pessoas(["Eva"])
        self.assertContains(self.client.get(painel), "Fora das linhas (1)")
        self.assertNotContains(self.client.get(publica), "Eva")

        # sortear de novo esconde até publicar outra vez
        self.client.post(reverse("votacao:painel_linhas_sortear"), {"por_linha": "10"})
        dados = arm.linhas()
        self.assertEqual((len(dados["linhas"]), dados["publicado"], dados["equilibrar"]), (1, False, False))
        self.assertContains(self.client.get(publica), "ainda não foram sorteadas")

    # ---------- sugestões de troca de linha (só conectado, só troca, em fila) ----------
    # Hoje estão desligadas (TROCAS_ATIVAS = False): só o organizador ajusta as linhas. Os testes marcados com
    # @mock.patch(..., True) conferem que o recurso continua funcionando, para quando for religado.

    def test_trocas_desligadas_so_o_organizador_ajusta(self):
        arm.salvar_sorteio([["ana", "bruno"], ["caio", "davi"]], 2, True)
        arm.publicar_linhas()
        # nada de sugestão na página pública
        self.assertNotContains(self.client.get(reverse("votacao:linhas")), reverse("votacao:sugerir_troca"))
        self.logar("ana")
        url = reverse("votacao:sugerir_troca")
        self.assertRedirects(self.client.get(url), reverse("votacao:linhas"))
        self.client.post(url, {"jogador": "ana", "com": "caio"})
        self.assertEqual(arm.pedidos(), [])
        # a senha com ?para=trocar só leva para as notas
        self.client.post(reverse("votacao:sair_jogador"))
        resposta = self.client.post(reverse("votacao:entrar", args=["ana"]) + "?para=trocar", {"senha": self.senha("ana")})
        self.assertRedirects(resposta, reverse("votacao:avaliar", args=["ana"]), fetch_redirect_response=False)
        # no painel, sem aba Trocas; o ajuste manual continua
        self.entrar()
        painel = self.client.get(reverse("votacao:painel_linhas"))
        self.assertNotContains(painel, reverse("votacao:painel_trocas"))
        self.assertNotContains(painel, 'class="menu cheio"')
        self.assertRedirects(self.client.get(reverse("votacao:painel_trocas")), reverse("votacao:painel_linhas"))
        arm.pedir_troca("ana", "troca", com="caio")
        pedido = arm.pedidos()[0]["id"]
        self.client.post(reverse("votacao:painel_troca_decidir", args=[pedido]), {"acao": "aprovar"})
        self.assertEqual(arm.linhas()["linhas"], [["ana", "bruno"], ["caio", "davi"]])  # nada aprovado por fora
        self.client.post(reverse("votacao:painel_linhas_ajustar"), {"linha_ana": "2"})
        self.assertEqual(arm.linhas()["linhas"], [["bruno"], ["caio", "davi", "ana"]])

    def sortear_e_publicar(self):
        arm.salvar_sorteio([["ana", "bruno"], ["caio", "davi"]], 2, True)
        arm.publicar_linhas()

    def sugerir(self, jogador, com, motivo="", follow=False):
        """Sugere como quem estiver conectado no self.client."""
        return self.client.post(reverse("votacao:sugerir_troca"),
                                {"jogador": jogador, "com": com, "motivo": motivo}, follow=follow)

    def pendentes(self):
        return [p for p in arm.pedidos() if p["status"] == "pendente"]

    def texto(self, resposta):
        """O texto que aparece na tela (sem as tags e com os espaços juntados)."""
        return " ".join(re.sub(r"<[^>]+>", " ", resposta.content.decode()).split())

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_so_sugere_com_linhas_publicadas(self):
        url = reverse("votacao:sugerir_troca")
        self.logar("ana")
        self.assertRedirects(self.client.get(url), reverse("votacao:linhas"))
        arm.salvar_sorteio([["ana", "bruno"], ["caio", "davi"]], 2, True)  # sorteado, mas não publicado
        self.assertRedirects(self.client.get(url), reverse("votacao:linhas"))
        arm.publicar_linhas()
        self.assertContains(self.client.get(reverse("votacao:linhas")), "Sugerir troca de linha")
        self.assertContains(self.client.get(url), "Trocar de lugar com quem?")

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_sugerir_exige_estar_conectado(self):
        self.sortear_e_publicar()
        url = reverse("votacao:sugerir_troca")
        pagina = self.client.get(url)
        self.assertContains(pagina, "entre com a sua senha")
        self.assertContains(pagina, reverse("votacao:entrar", args=["ana"]) + "?para=trocar")
        self.assertNotContains(pagina, 'name="com"')
        # sem estar conectado, não entra na fila
        self.client.post(url, {"jogador": "ana", "com": "caio"})
        self.assertEqual(arm.pedidos(), [])
        # a senha leva de volta para a sugestão
        tela_senha = reverse("votacao:entrar", args=["bruno"]) + "?para=trocar"
        self.assertContains(self.client.get(tela_senha), "para sugerir troca de linha")
        self.assertRedirects(self.client.post(tela_senha, {"senha": self.senha("bruno")}), url)
        self.assertContains(self.client.get(url), "Sugerindo como <strong>Bruno</strong>")

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_sugere_troca_de_outras_pessoas_e_fica_registrado_quem_sugeriu(self):
        self.sortear_e_publicar()
        self.logar("bruno")
        resposta = self.sugerir("ana", "caio", "os dois são primos")
        self.assertRedirects(resposta, reverse("votacao:linhas"))
        [pedido] = self.pendentes()
        self.assertEqual((pedido["jogador"], pedido["tipo"], pedido["com"], pedido["motivo"], pedido["por"]),
                         ("ana", "troca", "caio", "os dois são primos", "bruno"))

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_so_tem_troca_nao_tem_ir_para_linha(self):
        self.sortear_e_publicar()
        self.logar("ana")
        pagina = self.client.get(reverse("votacao:sugerir_troca"))
        self.assertNotContains(pagina, "Ir para a Linha")
        self.assertNotContains(pagina, "Quem está sugerindo")
        # mesmo mandando à mão um "ir para a linha", não entra
        self.client.post(reverse("votacao:sugerir_troca"), {"jogador": "ana", "pedido": "linha:2"})
        self.assertEqual(arm.pedidos(), [])

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_sugestao_invalida(self):
        self.sortear_e_publicar()
        self.logar("ana")
        self.assertContains(self.sugerir("ana", "bruno"), "Escolha com quem Ana troca de lugar")  # mesma linha
        self.assertContains(self.sugerir("ana", "ninguem"), "Escolha com quem Ana troca de lugar")
        self.assertContains(self.sugerir("ninguem", "caio"), "Escolha quem vai trocar de linha.")
        self.assertEqual(arm.pedidos(), [])

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_fila_nao_substitui_e_junta_repetidas(self):
        self.sortear_e_publicar()
        self.logar("ana")
        self.sugerir("ana", "caio")
        self.sugerir("ana", "davi")
        self.logar("bruno")
        resposta = self.sugerir("caio", "ana", follow=True)  # a mesma troca, na ordem inversa, de outra pessoa
        self.assertContains(resposta, "Essa troca já está na fila")
        self.assertEqual([(p["jogador"], p["com"]) for p in self.pendentes()], [("ana", "caio"), ("ana", "davi")])

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_fila_tem_teto(self):
        self.sortear_e_publicar()
        self.logar("ana")
        with mock.patch.object(arm, "MAX_PEDIDOS_NA_FILA", 2):
            self.sugerir("ana", "caio")
            self.sugerir("bruno", "davi")
            resposta = self.sugerir("ana", "davi", follow=True)
        self.assertContains(resposta, "A fila de sugestões está cheia")
        self.assertEqual(len(self.pendentes()), 2)

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_organizador_aprova_e_recusa_na_ordem_da_fila(self):
        self.sortear_e_publicar()
        self.logar("bruno")
        self.sugerir("ana", "caio", "por favor")
        self.logar("davi")
        self.sugerir("bruno", "davi")
        self.entrar()
        pagina = self.client.get(reverse("votacao:painel_trocas"))
        self.assertContains(pagina, '<span class="contador-menu" title="Sugestões de troca na fila">2</span>', html=True)
        tela = self.texto(pagina)
        # a frase de cada sugestão, na ordem da fila
        primeira = "Ana da Linha 1 vai pra Linha 2 no lugar de Caio Sugerido por: Bruno"
        segunda = "Bruno da Linha 1 vai pra Linha 2 no lugar de Davi Sugerido por: Davi"
        self.assertIn(primeira, tela)
        self.assertIn(segunda, tela)
        self.assertLess(tela.index(primeira), tela.index(segunda))
        self.assertIn("1º da fila", tela)
        self.assertIn("“por favor”", tela)
        # como está e como fica: quem sai e quem entra lado a lado, nas duas linhas
        self.assertIn("Linha 1 Como está Como fica Ana sai → Caio entra Bruno Bruno média", tela)
        self.assertIn("Linha 2 Como está Como fica Caio sai → Ana entra Davi Davi média", tela)
        self.assertContains(self.client.get(reverse("votacao:painel_linhas")), "2 sugestões de troca na fila")

        troca1, troca2 = self.pendentes()
        resposta = self.client.post(reverse("votacao:painel_troca_decidir", args=[troca1["id"]]), {"acao": "aprovar"})
        self.assertRedirects(resposta, reverse("votacao:painel_trocas"), fetch_redirect_response=False)
        self.assertEqual(arm.linhas()["linhas"], [["caio", "bruno"], ["ana", "davi"]])
        self.assertTrue(arm.linhas()["publicado"])
        self.client.post(reverse("votacao:painel_troca_decidir", args=[troca2["id"]]), {"acao": "recusar"})
        self.assertEqual(arm.linhas()["linhas"], [["caio", "bruno"], ["ana", "davi"]])
        self.assertEqual([p["status"] for p in arm.pedidos()], ["aprovado", "recusado"])
        tela = self.texto(self.client.get(reverse("votacao:painel_trocas")))
        self.assertIn("Nenhuma sugestão na fila", tela)
        self.assertIn("❌ Bruno ⇄ Davi · sugerido por Davi", tela)  # histórico, do mais novo para o mais antigo
        self.assertIn("✅ Ana ⇄ Caio · sugerido por Bruno", tela)

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_sugestao_antiga_de_mudar_de_linha_ainda_aparece_para_o_organizador(self):
        self.sortear_e_publicar()
        arm.pedir_troca("davi", "mudar", destino=1)  # feita na versão em que dava para "ir para a linha"
        self.entrar()
        tela = self.texto(self.client.get(reverse("votacao:painel_trocas")))
        self.assertIn("Davi da Linha 2 vai pra Linha 1 Sugerido por: (sem nome)", tela)
        # a Linha 2 perde o Davi e a Linha 1 ganha
        self.assertIn("Linha 2 Como está Como fica Caio Caio Davi sai → — média", tela)
        self.assertIn("Linha 1 Como está Como fica Ana Ana Bruno Bruno — → Davi entra média", tela)

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_sugestao_que_nao_da_mais_para_aplicar(self):
        self.sortear_e_publicar()
        self.logar("bruno")
        self.sugerir("ana", "caio")
        self.entrar()
        # o organizador colocou o Caio na linha da Ana antes de decidir
        self.client.post(reverse("votacao:painel_linhas_ajustar"), {"linha_caio": "1"})
        self.assertContains(self.client.get(reverse("votacao:painel_trocas")), "Não dá mais para aplicar")
        [pedido] = self.pendentes()
        resposta = self.client.post(reverse("votacao:painel_troca_decidir", args=[pedido["id"]]),
                                    {"acao": "aprovar"}, follow=True)
        self.assertContains(resposta, "Não dá mais para aplicar o pedido de Ana")
        self.assertEqual(len(self.pendentes()), 1)

    @mock.patch("votacao.views.TROCAS_ATIVAS", True)
    def test_sortear_de_novo_apaga_a_fila(self):
        self.sortear_e_publicar()
        self.logar("ana")
        self.sugerir("ana", "caio")
        self.entrar()
        self.client.post(reverse("votacao:painel_linhas_sortear"), {"por_linha": "2"})
        self.assertEqual(arm.pedidos(), [])

    # ---------- painel ----------

    def test_painel_exige_login(self):
        self.assertRedirects(self.client.get(reverse("votacao:painel")), reverse("votacao:painel_entrar"))
        self.assertRedirects(self.client.get(reverse("votacao:painel_linhas")), reverse("votacao:painel_entrar"))
        self.assertRedirects(self.client.get(reverse("votacao:painel_cadastro")), reverse("votacao:painel_entrar"))
        self.assertRedirects(self.client.get(reverse("votacao:painel_trocas")), reverse("votacao:painel_entrar"))
        arm.pedir_troca("ana", "troca", com="bruno")
        resposta = self.client.post(reverse("votacao:painel_troca_decidir", args=[arm.pedidos()[0]["id"]]),
                                    {"acao": "aprovar"})
        self.assertRedirects(resposta, reverse("votacao:painel_entrar"))
        self.assertEqual(arm.pedidos()[0]["status"], "pendente")
        arm.salvar_sorteio([["ana", "bruno"]], 2, True)
        arm.publicar_linhas()
        senha_ana = self.senha("ana")
        for nome in ["painel_adicionar", "painel_cadastro_salvar", "painel_excluir", "painel_zerar", "painel_remover_foto",
                     "painel_nova_senha", "painel_linhas_sortear", "painel_linhas_publicar",
                     "painel_linhas_despublicar", "painel_linhas_ajustar", "painel_linhas_regra",
                     "painel_fechar_semana", "painel_limpar_historico", "painel_ativo"]:
            resposta = self.client.post(reverse(f"votacao:{nome}"), {"nomes": "Hacker", "id": "ana", "ids": ["ana"],
                                                                     "a": "ana", "b": "bruno", "regra": "juntos"})
            self.assertRedirects(resposta, reverse("votacao:painel_entrar"))
        self.assertEqual(len(arm.jogadores()), 4)
        self.assertEqual(len(arm.ativos()), 4)
        self.assertEqual(arm.linhas()["juntos"], [])
        self.assertEqual(arm.historico(), [])
        self.assertEqual(self.senha("ana"), senha_ana)
        self.assertEqual((arm.linhas()["linhas"], arm.linhas()["publicado"]), ([["ana", "bruno"]], True))
        resposta = self.client.post(reverse("votacao:painel_entrar"), {"usuario": "chefe", "senha": "errada"})
        self.assertContains(resposta, "Usuário ou senha incorretos.")

    def test_painel(self):
        self.logar("ana")
        self.client.post(reverse("votacao:avaliar", args=["ana"]), {"nota_bruno": "1", "nota_caio": "5", "nota_davi": "3"})
        self.entrar()
        resposta = self.client.get(reverse("votacao:painel"))
        self.assertContains(resposta, "1 de 4 pessoas já avaliaram")
        self.assertContains(resposta, "<strong>5,00</strong>", html=True)
        # cadastro e zerar ficam na aba Cadastro, não na Semana
        self.assertContains(resposta, "Quem joga esta semana")
        self.assertNotContains(resposta, "form-cadastro")
        self.assertNotContains(resposta, "Zerar avaliações")
        cadastro = self.client.get(reverse("votacao:painel_cadastro"))
        self.assertContains(cadastro, 'id="form-cadastro"')
        self.assertContains(cadastro, "1 guardada agora")
        self.assertContains(cadastro, "Zerar avaliações</button>")

        self.client.post(reverse("votacao:painel_adicionar"), {"nomes": "Ana\nEva", "somente_estes": "on"})
        self.assertEqual([p["nome"] for p in arm.jogadores()], ["Ana", "Eva"])

        self.assertRedirects(self.client.post(reverse("votacao:painel_zerar")), reverse("votacao:painel_cadastro"))
        self.assertEqual(arm.avaliacoes(), {})

        self.assertRedirects(self.client.post(reverse("votacao:painel_excluir"), {"id": "eva"}),
                             reverse("votacao:painel_cadastro"))
        self.assertNotIn("Eva", self.joga())

        self.client.post(reverse("votacao:painel_sair"))
        self.assertRedirects(self.client.get(reverse("votacao:painel")), reverse("votacao:painel_entrar"))

    def test_copiar_linhas_para_o_whatsapp(self):
        arm.salvar_sorteio([["ana", "bruno"], ["caio", "davi"]], 2, True)
        self.assertNotContains(self.client.get(reverse("votacao:linhas")), "Copiar linhas")  # ainda não publicadas
        self.entrar()
        painel = self.client.get(reverse("votacao:painel_linhas"))
        self.assertContains(painel, "📋 Copiar linhas")  # o organizador já pode copiar antes de publicar
        arm.publicar_linhas()
        resposta = self.client.get(reverse("votacao:linhas"))
        self.assertContains(resposta, 'data-copiar="texto-whatsapp"')
        self.assertContains(resposta, 'href="https://wa.me/?text=')
        texto = json.loads(re.search(r'<script id="texto-whatsapp" type="application/json">(.*?)</script>',
                                     resposta.content.decode(), re.S).group(1))
        self.assertTrue(texto.startswith("⚽ *Racha do Doug* · linhas de "))
        self.assertIn("🟢 *Linha 1*\n• Ana\n• Bruno\n\n🟠 *Linha 2*\n• Caio\n• Davi", texto)
        self.assertTrue(texto.endswith("Bom jogo! 👉 http://testserver/linhas/"))
        # no link do WhatsApp vai o mesmo texto
        link = re.search(r'href="https://wa.me/\?text=([^"]+)"', resposta.content.decode()).group(1)
        self.assertEqual(unquote(link), texto)

    def test_barra_de_progresso_no_inicio(self):
        resposta = self.client.get(reverse("votacao:inicio"))
        self.assertContains(resposta, "🗳️ 0 de 4 já votaram")
        self.assertContains(resposta, 'aria-valuenow="0"')
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 7}})
        resposta = self.client.get(reverse("votacao:inicio"))
        self.assertContains(resposta, "🗳️ 1 de 4 já votaram")
        self.assertContains(resposta, 'style="width: 25%"')
        self.assertNotContains(resposta, "Todo mundo já votou")
        for quem in ["bruno", "caio", "davi"]:
            arm.salvar_avaliacao(quem, {"escala": 10, "notas": {"ana": 7}})
        resposta = self.client.get(reverse("votacao:inicio"))
        self.assertContains(resposta, "100%")
        self.assertContains(resposta, "🎉 Todo mundo já votou!")

    def test_fechar_a_semana(self):
        self.entrar()
        arm.salvar_avaliacao("ana", {"escala": 10, "notas": {"bruno": 8, "caio": 6}, "nao_conhece": ["davi"]})
        arm.salvar_avaliacao("bruno", {"escala": 10, "notas": {"ana": 7, "caio": 9}})
        arm.salvar_sorteio([["ana", "caio"], ["bruno", "davi"]], 2, True)
        arm.publicar_linhas()
        arm.adicionar_regra("juntos", "ana", "caio")
        painel = self.client.get(reverse("votacao:painel"))
        self.assertContains(painel, "Nenhuma semana fechada ainda")
        self.assertNotContains(painel, "Limpar histórico")

        # só guardar: nada mais muda
        resposta = self.client.post(reverse("votacao:painel_fechar_semana"), follow=True)
        self.assertRedirects(resposta, reverse("votacao:painel") + "#historico")
        self.assertContains(resposta, "📚 Semana guardada no histórico. Bora para a próxima! ⚽")
        [semana] = arm.historico()
        self.assertEqual((semana["pessoas"], semana["jogadores"], semana["votaram"]), (4, 4, 2))
        self.assertEqual([(r["nome"], r["nota"], r["qtd"]) for r in semana["ranking"]],
                         [("Bruno", 8.0, 1), ("Caio", 7.5, 2), ("Ana", 7.0, 1)])
        self.assertEqual(semana["sem_nota"], [{"id": "davi", "nome": "Davi"}])
        self.assertEqual(semana["linhas"], [["Ana", "Caio"], ["Bruno", "Davi"]])
        self.assertTrue(semana["data"])
        self.assertEqual(len(arm.avaliacoes()), 2)
        self.assertTrue(arm.linhas()["publicado"])
        # o histórico aparece no painel, com as médias
        self.assertContains(resposta, "2 de 4 votaram")
        self.assertContains(resposta, "🥇 Bruno · 🥈 Caio · 🥉 Ana")
        self.assertContains(resposta, "1º Bruno</span><span class=\"suave\">8,00 · 1 nota</span>")
        self.assertContains(resposta, "<strong>Linha 1:</strong> Ana, Caio")
        self.assertContains(resposta, "Sem nota: Davi")

        # guardar e já preparar a próxima
        self.client.post(reverse("votacao:painel_fechar_semana"),
                         {"apagar_linhas": "on", "zerar": "on", "limpar_lista": "on"})
        self.assertEqual(len(arm.historico()), 2)
        self.assertEqual(arm.avaliacoes(), {})
        self.assertEqual((arm.linhas()["linhas"], arm.linhas()["publicado"]), ([], False))
        self.assertEqual(arm.linhas()["juntos"], [["ana", "caio"]])  # as regras continuam
        self.assertEqual(arm.jogadores(), [])
        self.assertEqual(len(arm.pessoas()), 4)  # o cadastro continua
        # sem ninguém na lista, não guarda nada
        resposta = self.client.post(reverse("votacao:painel_fechar_semana"), follow=True)
        self.assertContains(resposta, "não há o que guardar")
        self.assertEqual(len(arm.historico()), 2)

        # limpar o histórico
        resposta = self.client.post(reverse("votacao:painel_limpar_historico"), follow=True)
        self.assertContains(resposta, "🧹 Histórico apagado.")
        self.assertEqual(arm.historico(), [])
        self.assertContains(resposta, "Nenhuma semana fechada ainda")

    def test_painel_mostra_notas_descartadas(self):
        nomes = [f"Pessoa {i}" for i in range(10)]
        arm.adicionar_pessoas(nomes)
        ids = [p["id"] for p in arm.jogadores() if p["id"].startswith("pessoa")]
        for i, quem in enumerate(ids):  # dez notas para a Ana: 1, 6 (oito vezes) e 10
            arm.salvar_avaliacao(quem, {"escala": 10, "notas": {"ana": [1, 6, 6, 6, 6, 6, 6, 6, 6, 10][i]}})
        self.entrar()
        resposta = self.client.get(reverse("votacao:painel"))
        self.assertContains(resposta, "<strong>6,00</strong><small>10 notas, 2 fora</small>", html=False)
        self.assertContains(resposta, "com 10 notas ou mais, a maior e a menor ficam de fora")
        self.assertContains(self.client.get(reverse("votacao:ranking")), "com 10 notas ou mais, a maior e a menor não contam")

    def test_painel_salvar_cadastro_com_foto(self):
        self.entrar()
        resposta = self.client.post(reverse("votacao:painel_cadastro_salvar"), {
            "ids": ["ana", "bruno", "caio", "davi"],
            "nome_ana": "Ana", "nome_bruno": "Bruninho", "nome_caio": "Caio", "nome_davi": "Davi",
            "joga": ["ana", "bruno"],
            "foto_ana": SimpleUploadedFile("ana.jpg", JPEG),
            "foto_caio": SimpleUploadedFile("caio.heic", b"nao-e-imagem"),
        }, follow=True)
        self.assertRedirects(resposta, reverse("votacao:painel_cadastro"))
        self.assertContains(resposta, "Não deu para usar estas fotos")
        self.assertEqual(self.joga(), {"Ana": True, "Bruninho": True, "Caio": False, "Davi": False})
        self.assertIsNotNone(arm.buscar_jogador("ana")["foto"])
