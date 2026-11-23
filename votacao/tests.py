import json
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from . import armazenamento as arm
from .notas import ler_nota, resultados

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


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
        arm.excluir_pessoa("bruno")
        self.assertNotIn("Bruno", self.joga())
        self.assertNotIn("bruno", arm.avaliacoes())
        self.assertEqual(arm.avaliacoes()["ana"]["notas"], {"caio": 0.5})
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
        self.assertContains(pagina, 'name="nota_bruno"', count=6)  # botões de 0 a 5
        resposta = self.client.post(url, {"nota_bruno": "0", "nota_caio": "5", "nota_davi": "3"})
        self.assertRedirects(resposta, reverse("votacao:inicio"))
        registro = self.json("avaliacoes.json")["avaliacoes"]["ana"]
        self.assertEqual(registro["notas"], {"bruno": 0, "caio": 5, "davi": 3})
        # ao voltar, as notas vêm marcadas
        self.assertContains(self.client.get(url), 'name="nota_davi" value="3" required aria-label="3" checked')

    def test_avaliar_faltando_ou_fora_do_limite(self):
        url = reverse("votacao:avaliar", args=["ana"])
        resposta = self.client.post(url, {"nota_bruno": "6", "nota_caio": "4", "nota_davi": "2.5"})
        self.assertContains(resposta, "Faltou dar nota para: Bruno, Davi.")
        self.assertContains(resposta, 'name="nota_caio" value="4" required aria-label="4" checked')  # não perde o que marcou
        self.assertEqual(arm.avaliacoes(), {})

    def test_avaliacao_antiga_so_preenche_notas_inteiras(self):
        arm.salvar_avaliacao("ana", {"modo": "ranking", "ranking": ["caio", "davi", "bruno"],
                                     "notas": {"caio": 5.0, "davi": 2.75, "bruno": 0.5}})
        pagina = self.client.get(reverse("votacao:avaliar", args=["ana"])).content.decode()
        self.assertEqual(pagina.count(" checked"), 1)
        self.assertIn('name="nota_caio" value="5" required aria-label="5" checked', pagina)

    # ---------- painel ----------

    def test_painel_exige_login(self):
        self.assertRedirects(self.client.get(reverse("votacao:painel")), reverse("votacao:painel_entrar"))
        for nome in ["painel_adicionar", "painel_cadastro", "painel_excluir", "painel_zerar", "painel_remover_foto"]:
            self.client.post(reverse(f"votacao:{nome}"), {"nomes": "Hacker", "id": "ana", "ids": ["ana"]})
        self.assertEqual(len(arm.jogadores()), 4)
        resposta = self.client.post(reverse("votacao:painel_entrar"), {"usuario": "chefe", "senha": "errada"})
        self.assertContains(resposta, "Usuário ou senha incorretos.")

    def test_painel(self):
        self.client.post(reverse("votacao:avaliar", args=["ana"]), {"nota_bruno": "1", "nota_caio": "5", "nota_davi": "3"})
        self.entrar()
        resposta = self.client.get(reverse("votacao:painel"))
        self.assertContains(resposta, "1 de 4 pessoas já avaliaram")
        self.assertContains(resposta, "<strong>5,00</strong>", html=True)

        self.client.post(reverse("votacao:painel_adicionar"), {"nomes": "Ana\nEva", "somente_estes": "on"})
        self.assertEqual([p["nome"] for p in arm.jogadores()], ["Ana", "Eva"])

        self.client.post(reverse("votacao:painel_zerar"))
        self.assertEqual(arm.avaliacoes(), {})

        self.client.post(reverse("votacao:painel_excluir"), {"id": "eva"})
        self.assertNotIn("Eva", self.joga())

        self.client.post(reverse("votacao:painel_sair"))
        self.assertRedirects(self.client.get(reverse("votacao:painel")), reverse("votacao:painel_entrar"))

    def test_painel_salvar_cadastro_com_foto(self):
        self.entrar()
        resposta = self.client.post(reverse("votacao:painel_cadastro"), {
            "ids": ["ana", "bruno", "caio", "davi"],
            "nome_ana": "Ana", "nome_bruno": "Bruninho", "nome_caio": "Caio", "nome_davi": "Davi",
            "joga": ["ana", "bruno"],
            "foto_ana": SimpleUploadedFile("ana.jpg", JPEG),
            "foto_caio": SimpleUploadedFile("caio.heic", b"nao-e-imagem"),
        }, follow=True)
        self.assertRedirects(resposta, reverse("votacao:painel"))
        self.assertContains(resposta, "Não deu para usar estas fotos")
        self.assertEqual(self.joga(), {"Ana": True, "Bruninho": True, "Caio": False, "Davi": False})
        self.assertIsNotNone(arm.buscar_jogador("ana")["foto"])
