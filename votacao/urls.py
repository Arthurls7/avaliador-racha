from django.urls import path

from . import views

app_name = "votacao"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("linhas/", views.ver_linhas, name="linhas"),
    path("ranking/", views.ver_ranking, name="ranking"),
    path("avaliar/<slug:pessoa_id>/", views.avaliar, name="avaliar"),
    path("avaliar/<slug:pessoa_id>/entrar/", views.entrar, name="entrar"),
    path("sair/", views.sair_jogador, name="sair_jogador"),
    path("media/fotos/<str:arquivo>", views.foto, name="foto"),
    path("painel/", views.painel, name="painel"),
    path("painel/entrar/", views.painel_entrar, name="painel_entrar"),
    path("painel/sair/", views.painel_sair, name="painel_sair"),
    path("painel/adicionar/", views.painel_adicionar, name="painel_adicionar"),
    path("painel/cadastro/", views.painel_cadastro, name="painel_cadastro"),
    path("painel/cadastro/salvar/", views.painel_cadastro_salvar, name="painel_cadastro_salvar"),
    path("painel/remover-foto/", views.painel_remover_foto, name="painel_remover_foto"),
    path("painel/nova-senha/", views.painel_nova_senha, name="painel_nova_senha"),
    path("painel/excluir/", views.painel_excluir, name="painel_excluir"),
    path("painel/zerar/", views.painel_zerar, name="painel_zerar"),
    path("painel/linhas/", views.painel_linhas, name="painel_linhas"),
    path("painel/linhas/sortear/", views.painel_linhas_sortear, name="painel_linhas_sortear"),
    path("painel/linhas/ajustar/", views.painel_linhas_ajustar, name="painel_linhas_ajustar"),
    path("painel/linhas/publicar/", views.painel_linhas_publicar, name="painel_linhas_publicar"),
    path("painel/linhas/despublicar/", views.painel_linhas_despublicar, name="painel_linhas_despublicar"),
]
