"""URL routes for playlist generation and persistence."""

from django.urls import path

from . import views

app_name = "recommender"

urlpatterns = [
    # POST endpoints used by the dashboard to produce and save playlists.
    path("generate/", views.generate_playlist, name="generate_playlist"),
    path("save/", views.save_playlist, name="save_playlist"),
]
