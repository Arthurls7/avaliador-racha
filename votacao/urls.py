from django.urls import path

from . import views

app_name = "votacao"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("avaliar/<slug:pessoa_id>/", views.avaliar, name="avaliar"),
    path("media/fotos/<str:arquivo>", views.foto, name="foto"),
    path("painel/", views.painel, name="painel"),
    path("painel/entrar/", views.painel_entrar, name="painel_entrar"),
    path("painel/sair/", views.painel_sair, name="painel_sair"),
    path("painel/adicionar/", views.painel_adicionar, name="painel_adicionar"),
    path("painel/cadastro/", views.painel_cadastro, name="painel_cadastro"),
    path("painel/remover-foto/", views.painel_remover_foto, name="painel_remover_foto"),
    path("painel/excluir/", views.painel_excluir, name="painel_excluir"),
    path("painel/zerar/", views.painel_zerar, name="painel_zerar"),
]
