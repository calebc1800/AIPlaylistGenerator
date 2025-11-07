"""Admin configuration for the recommender app."""

from django.contrib import admin

from .models import PlaylistGenerationStat, SavedPlaylist


@admin.register(SavedPlaylist)
class SavedPlaylistAdmin(admin.ModelAdmin):
    """Read-only admin definition for stored Spotify playlists."""

    list_display = ("playlist_id", "creator_user_id", "like_count")
    search_fields = ("playlist_id", "creator_user_id")


@admin.register(PlaylistGenerationStat)
class PlaylistGenerationStatAdmin(admin.ModelAdmin):
    """Admin configuration for generated playlist stats."""

    list_display = ("user_identifier", "track_count", "top_genre", "total_tokens", "created_at")
    search_fields = ("user_identifier", "top_genre", "prompt")
    list_filter = ("top_genre", "created_at")
    readonly_fields = ("created_at",)
