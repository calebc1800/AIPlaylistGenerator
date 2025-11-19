"""Serializers describing API payloads for the recommender endpoints."""

from rest_framework import serializers


class PlaylistGenerationRequestSerializer(serializers.Serializer):
    """Validate playlist generation requests."""

    prompt = serializers.CharField(max_length=1000)
    selected_artist_ids = serializers.ListField(
        child=serializers.CharField(max_length=128),
        required=False,
        allow_empty=True,
    )
    selected_artist_names = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        allow_empty=True,
    )
    llm_provider = serializers.CharField(required=False, allow_blank=True)

    def validate_selected_artist_ids(self, value):
        return [item.strip() for item in value if item and item.strip()]

    def validate_selected_artist_names(self, value):
        return [item.strip() for item in value if item and item.strip()]
