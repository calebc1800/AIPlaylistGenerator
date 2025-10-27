from django.urls import path

from . import views

app_name = "recommender"

urlpatterns = [
    path("generate/", views.generate_playlist, name="generate_playlist"),
]
