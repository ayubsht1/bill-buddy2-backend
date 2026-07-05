# urls.py
from django.urls import path
from django.http import HttpResponse

def groups_text(request):
    return HttpResponse("groups", content_type="text/plain")

urlpatterns = [
    path("groups/", groups_text),
]
