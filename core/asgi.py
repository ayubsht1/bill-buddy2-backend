
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from core.authentication import JWTAuthMiddleware
from groups.routing import websocket_urlpatterns as group_ws
from bill_buddy.routing import websocket_urlpatterns as personal_ws

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": JWTAuthMiddleware(
        URLRouter(group_ws + personal_ws)
    ),
})
