"""URL routes for playlist generation and persistence."""

from django.urls import path

from . import views

app_name = "recommender"

urlpatterns = [
    # POST endpoints used by the dashboard to produce and save playlists.
    path("generate/", views.generate_playlist, name="generate_playlist"),
    path("remix/", views.remix_playlist, name="remix_playlist"),
    path("cache/update/", views.update_cached_playlist, name="update_cached_playlist"),
    path("save/", views.save_playlist, name="save_playlist"),
    # GET endpoint for searching songs
    path("search/", views.search_songs, name="search_songs"),
    # POST endpoint for adding songs to playlist
    path("add-song/", views.add_song_to_playlist, name="add_song_to_playlist"),
    # POST endpoint for generating cover images
    path("generate-cover/", views.generate_cover_image, name="generate_cover_image"),
    # POST endpoint for caching cover image URL
    path("cache-cover/", views.cache_cover_image, name="cache_cover_image"),
]
