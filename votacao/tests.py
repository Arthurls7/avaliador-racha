import json
import re
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from . import armazenamento as arm
from .notas import ler_nota, resultados
from .sorteio import forcas, sortear, tamanhos

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class SorteioTest(SimpleTestCase):
    def test_tamanhos(self):
        self.assertEqual(tamanhos(17, 5), [5, 5, 5, 2])
        self.assertEqual(tamanhos(10, 5), [5, 5])
        self.assertEqual(tamanhos(3, 5), [3])
        self.assertEqual(tamanhos(0, 5), [])

    def test_todo_mundo_em_uma_linha_so(self):
        ids = [f"j{i}" for i in range(17)]
        linhas = sortear(ids, {j: i % 5 for i, j in enumerate(ids)}, 5)
        self.assertEqual(sorted(sum(linhas, [])), sorted(ids))
        self.assertEqual(sorted(len(l) for l in linhas), [2, 5, 5, 5])

    def test_equilibra_pelas_notas(self):
        forca = {"a": 5, "b": 5, "c": 5, "d": 5, "e": 1, "f": 1, "g": 1, "h": 1}
        for _ in range(5):
            linhas = sortear(list(forca), forca, 4)
            self.assertEqual([sum(forca[j] for j in l) / 4 for l in linhas], [3, 3])

    def test_sortear_de_novo_muda(self):
        ids = [f"j{i}" for i in range(10)]
        sorteios = {tuple(sorted(tuple(sorted(l)) for l in sortear(ids, dict.fromkeys(ids, 3), 5))) for _ in range(10)}
        self.assertGreater(len(sorteios), 1)

    def test_quem_nao_tem_nota_conta_como_media_geral(self):
        self.assertEqual(forcas(["a", "b", "c"], {"a": 4, "b": 2, "c": None}), {"a": 4, "b": 2, "c": 3})


class NotasTest(SimpleTestCase):
    def test_nota_e_inteiro_de_0_a_5(self):
        self.assertEqual([ler_nota(v) for v in ["0", "3", "5"]], [0, 3, 5])
        for invalido in ["6", "-1", "2.5", "", None, "abc"]:
            self.assertIsNone(ler_nota(invalido))

    def test_resultado_e_media_das_notas_recebidas(self):
        pessoas = [{"id": i, "nome": i.upper()} for i in "abc"]
        avaliacoes = {
            "a": {"notas": {"b": 5, "c": 1}},
            "b": {"notas": {"a": 3, "c": 2}},
            "c": {"modo": "ranking", "ranking": ["a", "b"], "notas": {"a": 5, "b": 0.5}},  # formato antigo ainda conta
            "saiu": {"notas": {"a": 0}},  # não joga esta semana
        }
        linhas = {l["id"]: l for l in resultados(pessoas, avaliacoes)}
        self.assertEqual(linhas["a"]["nota"], 4)
        self.assertEqual(linhas["a"]["qtd"], 2)
        self.assertEqual(linhas["b"]["nota"], 2.75)
        self.assertEqual(linhas["c"]["nota"], 1.5)
        self.assertEqual([l["id"] for l in resultados(pessoas, avaliacoes)], ["a", "b", "c"])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class FluxoTest(SimpleTestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)
        ajuste = override_settings(VOTACAO_DADOS_DIR=self.pasta / "dados", MEDIA_ROOT=self.pasta / "media")
        ajuste.enable()
        self.addCleanup(ajuste.disable)
        arm.adicionar_pessoas(["Davi", "Ana", "Caio", "Bruno"])
        arm.definir_admin("chefe", "segredo")

    def json(self, arquivo):
        return json.loads((self.pasta / "dados" / arquivo).read_text(encoding="utf-8"))

    def entrar(self):
        self.client.post(reverse("votacao:painel_entrar"), {"usuario": "chefe", "senha": "segredo"})

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
        novos, existentes = arm.adicionar_pessoas(["ana", "Eva"])
        self.assertEqual((novos, existentes), (1, 1))
        self.assertEqual(self.joga(), {"Ana": True, "Bruno": False, "Caio": False, "Davi": False, "Eva": True})

    def test_lista_completa_da_semana(self):
        arm.adicionar_pessoas(["Bruno", "Eva"], somente_estes=True)
        self.assertEqual(self.joga(), {"Ana": False, "Bruno": True, "Caio": False, "Davi": False, "Eva": True})

    def test_quem_nao_joga_nao_aparece_nem_avalia(self):
        arm.adicionar_pessoas(["Ana", "Bruno", "Caio"], somente_estes=True)
        self.assertNotContains(self.client.get(reverse("votacao:inicio")), "Davi")
        self.assertRedirects(self.client.get(reverse("votacao:avaliar", args=["davi"])),
                             reverse("votacao:inicio"))
        self.assertNotContains(self.client.get(reverse("votacao:avaliar", args=["ana"])), "nota_davi")

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
        self.assertEqual(arm.jogadores(), [{"id": "ze", "nome": "Zé", "foto": None, "joga": True}])

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
        pagina = self.client.get(url)
        self.assertNotContains(pagina, "nota_ana")  # não avalia a si mesmo
        self.assertContains(pagina, 'name="nota_bruno"', count=7)  # botões de 0 a 5 + "Não conheço"
        self.assertNotContains(pagina, "Nota anterior")  # primeira vez: nada para mostrar
        resposta = self.client.post(url, {"nota_bruno": "0", "nota_caio": "5", "nota_davi": "3"})
        self.assertRedirects(resposta, reverse("votacao:inicio"))
        registro = self.json("avaliacoes.json")["avaliacoes"]["ana"]
        self.assertEqual(registro["notas"], {"bruno": 0, "caio": 5, "davi": 3})
        self.assertEqual(registro["nao_conhece"], [])
        # ao voltar, as notas vêm marcadas e a nota anterior aparece em cada cartão
        pagina = self.client.get(url)
        self.assertContains(pagina, 'name="nota_davi" value="3" required aria-label="3" checked')
        for nota in ["0", "5", "3"]:
            self.assertContains(pagina, f"Nota anterior: <b>{nota}</b>")

    def test_nao_conheco(self):
        url = reverse("votacao:avaliar", args=["ana"])
        resposta = self.client.post(url, {"nota_bruno": "nao-conheco", "nota_caio": "4", "nota_davi": "nao-conheco"})
        self.assertRedirects(resposta, reverse("votacao:inicio"))
        registro = self.json("avaliacoes.json")["avaliacoes"]["ana"]
        self.assertEqual(registro["notas"], {"caio": 4})
        self.assertEqual(registro["nao_conhece"], ["bruno", "davi"])
        # "Não conheço" fica fora da média
        arm.salvar_avaliacao("caio", {"notas": {"bruno": 2, "davi": 1, "ana": 3}})
        linhas = {l["id"]: l for l in resultados(arm.jogadores(), arm.avaliacoes())}
        self.assertEqual((linhas["bruno"]["nota"], linhas["bruno"]["qtd"]), (2, 1))
        self.assertEqual((linhas["caio"]["nota"], linhas["caio"]["qtd"]), (4, 1))
        # ao voltar, "Não conheço" vem marcado
        pagina = self.client.get(url)
        self.assertContains(pagina, 'name="nota_bruno" value="nao-conheco" required checked')
        self.assertContains(pagina, "Nota anterior: <b>não conheço</b>", count=2)

    def test_avaliar_faltando_ou_fora_do_limite(self):
        url = reverse("votacao:avaliar", args=["ana"])
        resposta = self.client.post(url, {"nota_bruno": "6", "nota_caio": "4", "nota_davi": "2.5"})
        self.assertContains(resposta, "para: Bruno, Davi.")
        self.assertContains(resposta, 'name="nota_caio" value="4" required aria-label="4" checked')  # não perde o que marcou
        self.assertEqual(arm.avaliacoes(), {})

    def test_avaliacao_antiga_so_preenche_notas_inteiras(self):
        arm.salvar_avaliacao("ana", {"modo": "ranking", "ranking": ["caio", "davi", "bruno"],
                                     "notas": {"caio": 5.0, "davi": 2.75, "bruno": 0.5}})
        pagina = self.client.get(reverse("votacao:avaliar", args=["ana"])).content.decode()
        self.assertEqual(pagina.count(" checked"), 1)
        self.assertIn('name="nota_caio" value="5" required aria-label="5" checked', pagina)
        # as quebradas não viram botão, mas aparecem como nota anterior
        self.assertIn("Nota anterior: <b>2,75</b>", pagina)
        self.assertIn("Nota anterior: <b>0,5</b>", pagina)

    # ---------- abas públicas: linhas e ranking ----------

    def test_abas_publicas(self):
        for nome in ["inicio", "linhas", "ranking"]:
            resposta = self.client.get(reverse(f"votacao:{nome}"))
            self.assertContains(resposta, f'href="{reverse(f"votacao:{nome}")}" class="ativa" aria-current="page"')
            for outra in ["inicio", "linhas", "ranking"]:
                self.assertContains(resposta, f'href="{reverse(f"votacao:{outra}")}"')

    def test_cabecalho_racha_do_doug(self):
        for url in [reverse("votacao:inicio"), reverse("votacao:avaliar", args=["ana"]), reverse("votacao:painel_entrar")]:
            resposta = self.client.get(url)
            self.assertContains(resposta, "Racha do Doug</span>")
            self.assertContains(resposta, "· Racha do Doug</title>")
            self.assertContains(resposta, 'aria-label="Área do organizador"')
        # na tela de avaliar, o menu marca "Avaliar"
        self.assertContains(self.client.get(reverse("votacao:avaliar", args=["ana"])),
                            f'href="{reverse("votacao:inicio")}" class="ativa" aria-current="page"')
        self.entrar()
        for nome in ["painel", "painel_linhas", "painel_cadastro"]:
            resposta = self.client.get(reverse(f"votacao:{nome}"))
            self.assertContains(resposta, "Racha do Doug<small>Organizador</small>")
            self.assertContains(resposta, f'href="{reverse(f"votacao:{nome}")}" class="ativa" aria-current="page"')
            self.assertContains(resposta, 'aria-label="Ver o site"')
            self.assertContains(resposta, f'action="{reverse("votacao:painel_sair")}"')

    def test_ranking_publico_sem_media(self):
        arm.salvar_avaliacao("ana", {"notas": {"bruno": 5, "caio": 2, "davi": 2}})
        arm.salvar_avaliacao("bruno", {"notas": {"ana": 3, "caio": 4, "davi": 4}})
        # médias: Bruno 5; Ana, Caio e Davi 3 (empatados)
        resposta = self.client.get(reverse("votacao:ranking"))
        pagina = resposta.content.decode()
        self.assertEqual(re.findall(r'<span class="nome">([^<]+)</span>', pagina), ["Bruno", "Ana", "Caio", "Davi"])
        self.assertEqual(pagina.count("🥇"), 1)
        self.assertEqual(pagina.count("🥈"), 3)  # empate = mesma posição
        for media in ["5,00", "3,00", "5,0", "3,0"]:
            self.assertNotIn(media, pagina)
        self.assertContains(resposta, "2 de 4 já avaliaram")

    def test_ranking_publico_quem_nao_tem_nota(self):
        arm.salvar_avaliacao("ana", {"notas": {"bruno": 5}, "nao_conhece": ["caio", "davi"]})
        resposta = self.client.get(reverse("votacao:ranking"))
        self.assertContains(resposta, "Ainda sem nota")
        self.assertContains(self.client.get(reverse("votacao:ranking")), "🥇", count=1)

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

    # ---------- painel ----------

    def test_painel_exige_login(self):
        self.assertRedirects(self.client.get(reverse("votacao:painel")), reverse("votacao:painel_entrar"))
        self.assertRedirects(self.client.get(reverse("votacao:painel_linhas")), reverse("votacao:painel_entrar"))
        self.assertRedirects(self.client.get(reverse("votacao:painel_cadastro")), reverse("votacao:painel_entrar"))
        arm.salvar_sorteio([["ana", "bruno"]], 2, True)
        arm.publicar_linhas()
        for nome in ["painel_adicionar", "painel_cadastro_salvar", "painel_excluir", "painel_zerar", "painel_remover_foto",
                     "painel_linhas_sortear", "painel_linhas_publicar", "painel_linhas_despublicar",
                     "painel_linhas_ajustar"]:
            resposta = self.client.post(reverse(f"votacao:{nome}"), {"nomes": "Hacker", "id": "ana", "ids": ["ana"]})
            self.assertRedirects(resposta, reverse("votacao:painel_entrar"))
        self.assertEqual(len(arm.jogadores()), 4)
        self.assertEqual((arm.linhas()["linhas"], arm.linhas()["publicado"]), ([["ana", "bruno"]], True))
        resposta = self.client.post(reverse("votacao:painel_entrar"), {"usuario": "chefe", "senha": "errada"})
        self.assertContains(resposta, "Usuário ou senha incorretos.")

    def test_painel(self):
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
