from django.urls import path
from .views import DashboardView, UserStatsAPIView

app_name = 'dashboard'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('api/user-stats/', UserStatsAPIView.as_view(), name='user-stats'),
]
