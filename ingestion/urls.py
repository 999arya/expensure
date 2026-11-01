from django.urls import path
from . import views

app_name = "ingestion"

urlpatterns = [
    path("paste/", views.paste_view, name="paste"),
    path("paste/commit/", views.commit_paste_view, name="paste_commit"),
    path("shortcuts/", views.shortcuts_guide, name="shortcuts"),
    path("tokens/generate/", views.generate_token, name="token_generate"),
    path("tokens/<int:pk>/revoke/", views.revoke_token, name="token_revoke"),
    path("api/ingest/", views.api_ingest, name="api_ingest"),
]
