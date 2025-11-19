"""API endpoints for playlist generation and related flows."""

import json

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import views as recommender_views
from .serializers import PlaylistGenerationRequestSerializer


class PlaylistGenerationAPIView(APIView):
    """Expose the playlist generation flow as a JSON endpoint."""

    def post(self, request, *args, **kwargs):  # noqa: D401 - DRF signature
        serializer = PlaylistGenerationRequestSerializer(data=request.data or {})
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        payload = {
            "prompt": data["prompt"].strip(),
            "selected_artist_ids": data.get("selected_artist_ids") or [],
            "selected_artist_names": data.get("selected_artist_names") or [],
        }
        if data.get("llm_provider"):
            payload["llm_provider"] = data["llm_provider"]

        django_request = getattr(request, "_request", request)
        body = json.dumps(payload).encode("utf-8")
        django_request._body = body  # pylint: disable=protected-access
        django_request.META["CONTENT_TYPE"] = "application/json"
        django_request.META["CONTENT_LENGTH"] = str(len(body))
        return recommender_views.generate_playlist(django_request)
