import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
# import chat.routing # Uncomment this once you create your chat routing file

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
application = ProtocolTypeRouter({
    # Handles normal HTTP requests (Django REST Framework)
    "http": get_asgi_application(),
    
    # Handles WebSocket connections (Next.js real-time chat)
    "websocket": AuthMiddlewareStack(
        URLRouter(
            [] # Pass an empty list here so Channels doesn't crash
        )
    ),
})