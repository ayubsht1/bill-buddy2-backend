import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack   # or your TokenAuthMiddleware

from groups.routing import websocket_urlpatterns as group_ws
from bill_buddy.routing import websocket_urlpatterns as personal_ws

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(group_ws + personal_ws)
    ),
})