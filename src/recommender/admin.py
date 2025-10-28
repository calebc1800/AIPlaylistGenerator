"""Admin configuration for the recommender app."""

from django.contrib import admin

from .models import SavedPlaylist


@admin.register(SavedPlaylist)
class SavedPlaylistAdmin(admin.ModelAdmin):
    list_display = ("playlist_id", "creator_user_id", "like_count")
    search_fields = ("playlist_id", "creator_user_id")
