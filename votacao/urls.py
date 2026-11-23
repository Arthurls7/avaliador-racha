from django.urls import path

from . import views

app_name = "votacao"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("avaliar/<slug:pessoa_id>/", views.escolher_modo, name="escolher_modo"),
    path("avaliar/<slug:pessoa_id>/ranking/", views.avaliar_ranking, name="ranking"),
    path("avaliar/<slug:pessoa_id>/notas/", views.avaliar_notas, name="notas"),
    path("painel/", views.painel, name="painel"),
    path("painel/entrar/", views.painel_entrar, name="painel_entrar"),
    path("painel/sair/", views.painel_sair, name="painel_sair"),
    path("painel/pessoas/", views.painel_pessoas, name="painel_pessoas"),
    path("painel/zerar/", views.painel_zerar, name="painel_zerar"),
]
