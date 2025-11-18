from django.urls import path
from .views import (
    DashboardView,
    UserStatsAPIView,
    ListeningSuggestionsAPIView,
    RecommendedArtistsAPIView,
)

app_name = 'dashboard'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('api/user-stats/', UserStatsAPIView.as_view(), name='user-stats'),
    path('api/listening-suggestions/', ListeningSuggestionsAPIView.as_view(), name='listening-suggestions'),
    path('api/recommended-artists/', RecommendedArtistsAPIView.as_view(), name='recommended-artists'),
]
