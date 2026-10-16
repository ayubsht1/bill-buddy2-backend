# routing.py
from django.urls import re_path
from .consumers import PersonalChatConsumer

websocket_urlpatterns = [
    re_path(r'ws/personal-chat/$', PersonalChatConsumer.as_asgi()),  # new
]