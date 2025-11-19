"""URL config for recommender REST endpoints."""

from django.urls import path

from .views import PlaylistGenerationAPIView

app_name = "recommender_api"

urlpatterns = [
    path(
        "playlists/generate/",
        PlaylistGenerationAPIView.as_view(),
        name="generate",
    ),
]
