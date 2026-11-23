import getpass

from django.core.management.base import BaseCommand, CommandError

from votacao import armazenamento as arm


class Command(BaseCommand):
    help = "Define o usuário e a senha do administrador (grava em dados/logins.json)."

    def add_arguments(self, parser):
        parser.add_argument("usuario")
        parser.add_argument("--senha", help="Se não for passada, a senha é pedida no terminal.")

    def handle(self, usuario, senha=None, **opcoes):
        if not senha:
            senha = getpass.getpass("Senha: ")
            if senha != getpass.getpass("Repita a senha: "):
                raise CommandError("As senhas não conferem.")
        if not senha:
            raise CommandError("A senha não pode ser vazia.")
        arm.definir_admin(usuario, senha)
        self.stdout.write(self.style.SUCCESS(f"Administrador '{usuario}' definido."))
