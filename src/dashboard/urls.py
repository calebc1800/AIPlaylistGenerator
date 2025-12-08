"""
Docstring for src.dashboard.urls
"""
from django.urls import path
from .views import (
    DashboardView,
    UserStatsAPIView,
    ListeningSuggestionsAPIView,
    RecommendedArtistsAPIView,
    toggle_follow,
    get_following_list,
    get_user_playlists
)

app_name = 'dashboard'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('api/user-stats/', UserStatsAPIView.as_view(), name='user-stats'),

    path('api/listening-suggestions/', ListeningSuggestionsAPIView.as_view(),
         name='listening-suggestions'),

    path('api/recommended-artists/', RecommendedArtistsAPIView.as_view(),
         name='recommended-artists'),

    path('api/follow/toggle/', toggle_follow, name='toggle-follow'),

    path('api/following/', get_following_list, name='get-following'),

    path('api/user/<str:user_id>/playlists/', get_user_playlists, name='user-playlists'),
]
