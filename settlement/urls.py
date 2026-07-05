from django.urls import path
from django.http import HttpResponse

def settlement_text(request):
    return HttpResponse("settlement", content_type="text/plain")

urlpatterns = [
    path("settlement/", settlement_text),
]
