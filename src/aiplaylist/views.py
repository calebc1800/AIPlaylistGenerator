from django.shortcuts import render
from django.views import View


class HomeView(View):
    """Display the main landing page"""

    def get(self, request):
        context = {}
        return render(request, 'index.html', context)
