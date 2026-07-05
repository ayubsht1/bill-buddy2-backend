from django.urls import path
from django.http import HttpResponse

def expense_text(request):
    return HttpResponse("expense", content_type="text/plain")

urlpatterns = [
    path("expense/", expense_text),
]
