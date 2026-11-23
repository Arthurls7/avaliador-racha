import json
import shutil
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from . import armazenamento as arm
from .notas import notas_por_ranking, resultados


class NotasTest(SimpleTestCase):
    def test_ranking_vai_de_5_a_meio(self):
        self.assertEqual(list(notas_por_ranking(["a", "b", "c", "d"]).values()), [5, 3.5, 2, 0.5])
        self.assertEqual(notas_por_ranking(["a", "b"]), {"a": 5, "b": 0.5})
        self.assertEqual(notas_por_ranking(["a"]), {"a": 5})

    def test_resultado_e_media_das_notas_recebidas(self):
        pessoas = [{"id": i, "nome": i.upper()} for i in "abc"]
        avaliacoes = {
            "a": {"modo": "ranking", "ranking": ["b", "c"], "notas": {"b": 5, "c": 0.5}},
            "b": {"modo": "ataque_defesa", "ataque": {"a": 4, "c": 2}, "defesa": {"a": 2, "c": 1},
                  "notas": {"a": 3, "c": 1.5}},
            "saiu": {"modo": "ranking", "ranking": ["a"], "notas": {"a": 5}},  # não está mais na lista
        }
        linhas = {l["id"]: l for l in resultados(pessoas, avaliacoes)}
        self.assertEqual(linhas["c"]["nota"], 1.0)
        self.assertEqual(linhas["c"]["qtd"], 2)
        self.assertEqual(linhas["c"]["ataque"], 2)
        self.assertEqual(linhas["a"]["nota"], 3)
        self.assertEqual(linhas["b"]["nota"], 5)
        self.assertIsNone(linhas["b"]["ataque"])
        self.assertEqual([l["id"] for l in resultados(pessoas, avaliacoes)], ["b", "a", "c"])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class FluxoTest(SimpleTestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)
        ajuste = override_settings(VOTACAO_DADOS_DIR=self.pasta)
        ajuste.enable()
        self.addCleanup(ajuste.disable)
        arm.definir_pessoas(["Ana", "Bruno", "Caio", "Davi"])
        arm.definir_admin("chefe", "segredo")

    def json(self, arquivo):
        return json.loads((self.pasta / arquivo).read_text(encoding="utf-8"))

    def test_lista_de_nomes(self):
        texto = "1. Ana\n2 - Bruno \n\n3) caio\nCaio\n  Davi  Souza "
        self.assertEqual(arm.limpar_nomes(texto), ["Ana", "Bruno", "caio", "Davi Souza"])

    def test_salvar_lista_mantem_ids(self):
        antes = {p["nome"]: p["id"] for p in arm.pessoas()}
        arm.definir_pessoas(["Bruno", "Ana", "Eva", "Eva Maria"])
        depois = {p["nome"]: p["id"] for p in arm.pessoas()}
        self.assertEqual(depois["Ana"], antes["Ana"])
        self.assertEqual(depois["Bruno"], antes["Bruno"])
        self.assertEqual(set(depois.values()), {"ana", "bruno", "eva", "eva-maria"})

    def test_inicio_lista_pessoas(self):
        resposta = self.client.get(reverse("votacao:inicio"))
        self.assertContains(resposta, "Bruno")
        self.assertContains(resposta, reverse("votacao:escolher_modo", args=["ana"]))

    def test_ranking(self):
        url = reverse("votacao:ranking", args=["ana"])
        self.assertNotContains(self.client.get(url), '"id": "ana"')  # não avalia a si mesmo
        resposta = self.client.post(url, {"ranking": ["caio", "davi", "bruno"]})
        self.assertRedirects(resposta, reverse("votacao:inicio"))
        registro = self.json("avaliacoes.json")["avaliacoes"]["ana"]
        self.assertEqual(registro["modo"], "ranking")
        self.assertEqual(registro["notas"], {"caio": 5, "davi": 2.75, "bruno": 0.5})

    def test_ranking_incompleto_nao_salva(self):
        url = reverse("votacao:ranking", args=["ana"])
        self.client.post(url, {"ranking": ["caio", "davi"]})
        self.client.post(url, {"ranking": ["caio", "davi", "ana"]})
        self.assertEqual(arm.avaliacoes(), {})

    def test_ataque_defesa(self):
        url = reverse("votacao:notas", args=["bruno"])
        dados = {"ataque_ana": "4", "defesa_ana": "3", "ataque_caio": "5", "defesa_caio": "5",
                 "ataque_davi": "0", "defesa_davi": "1.5"}
        self.assertRedirects(self.client.post(url, dados), reverse("votacao:inicio"))
        registro = self.json("avaliacoes.json")["avaliacoes"]["bruno"]
        self.assertEqual(registro["notas"], {"ana": 3.5, "caio": 5, "davi": 0.75})
        # ao voltar, o formulário vem preenchido
        self.assertContains(self.client.get(url), '<option value="1.5" selected>1,5</option>', html=True)

    def test_ataque_defesa_faltando_ou_fora_do_limite(self):
        url = reverse("votacao:notas", args=["bruno"])
        resposta = self.client.post(url, {"ataque_ana": "4", "defesa_ana": "6", "ataque_caio": "5",
                                          "defesa_caio": "5"})
        self.assertContains(resposta, "Faltou dar nota de ataque e defesa para: Ana, Davi.")
        self.assertEqual(arm.avaliacoes(), {})

    def test_pessoa_inexistente(self):
        self.assertEqual(self.client.get(reverse("votacao:escolher_modo", args=["ninguem"])).status_code, 404)

    def test_painel_exige_login(self):
        self.assertRedirects(self.client.get(reverse("votacao:painel")), reverse("votacao:painel_entrar"))
        self.client.post(reverse("votacao:painel_pessoas"), {"nomes": "Hacker"})
        self.assertEqual(len(arm.pessoas()), 4)
        resposta = self.client.post(reverse("votacao:painel_entrar"), {"usuario": "chefe", "senha": "errada"})
        self.assertContains(resposta, "Usuário ou senha incorretos.")

    def test_painel(self):
        self.client.post(reverse("votacao:ranking", args=["ana"]), {"ranking": ["caio", "davi", "bruno"]})
        entrar = self.client.post(reverse("votacao:painel_entrar"), {"usuario": "chefe", "senha": "segredo"})
        self.assertRedirects(entrar, reverse("votacao:painel"))

        resposta = self.client.get(reverse("votacao:painel"))
        self.assertContains(resposta, "1 de 4 pessoas já avaliaram")
        self.assertContains(resposta, "<strong>5,00</strong>", html=True)

        self.client.post(reverse("votacao:painel_pessoas"), {"nomes": "Ana\nBruno\nEva"})
        self.assertEqual([p["nome"] for p in self.json("logins.json")["pessoas"]], ["Ana", "Bruno", "Eva"])

        self.client.post(reverse("votacao:painel_zerar"))
        self.assertEqual(arm.avaliacoes(), {})

        self.client.post(reverse("votacao:painel_sair"))
        self.assertRedirects(self.client.get(reverse("votacao:painel")), reverse("votacao:painel_entrar"))
