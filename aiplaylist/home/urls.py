from django.contrib import admin
from django.urls import path, include
from home import views as home_views  # Add this import

urlpatterns = [
    path('', views.home, name='home'),
    path('search/', views.search, name='search'),
    path('profile/<int:user_id>/', views.profile, name='profile'),
    path('accounts/login/', views.login, name='login'),
    path('accounts/logout/', views.logout, name='logout'),
    path('admin/', admin.site.urls),
    path('spotify/', include('spotify_auth.urls')),
]