from django.contrib import admin
from django.urls import path, include
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('search/', views.search, name='search'),
    path('profile/<int:user_id>/', views.profile, name='profile'),
    path('accounts/logout/', views.logout, name='logout'),
]