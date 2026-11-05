# groups/routing.py
from django.urls import re_path
from .consumers import GroupChatConsumer

websocket_urlpatterns = [
    re_path(
        r'ws/chat/(?P<group_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})/$',
        GroupChatConsumer.as_asgi(),
    ),
]