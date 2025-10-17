from django.urls import path
from .views import (
    SpotifyLoginView, 
    SpotifyCallbackView, 
    SpotifyRefreshTokenView,
    SpotifyDashboardView
)

app_name = 'spotify_auth'

urlpatterns = [
    path('login/', SpotifyLoginView.as_view(), name='login'),
    path('callback/', SpotifyCallbackView.as_view(), name='callback'),
    path('refresh/', SpotifyRefreshTokenView.as_view(), name='refresh'),
    path('dashboard/', SpotifyDashboardView.as_view(), name='dashboard'),
]