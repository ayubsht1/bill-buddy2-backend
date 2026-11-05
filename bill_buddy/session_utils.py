import re
from ipaddress import ip_address

from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .models import UserSession


def describe_user_agent(user_agent):
    if not user_agent:
        return "Unknown device", "Unknown browser", "Unknown OS"

    browser_patterns = (
        (r"Edg(?:e|A|iOS)?/([\d.]+)", "Microsoft Edge"),
        (r"OPR/([\d.]+)", "Opera"),
        (r"Chrome/([\d.]+)", "Chrome"),
        (r"Firefox/([\d.]+)", "Firefox"),
        (r"Version/([\d.]+).*Safari", "Safari"),
    )
    browser = "Unknown browser"
    for pattern, name in browser_patterns:
        match = re.search(pattern, user_agent, re.IGNORECASE)
        if match:
            browser = f"{name} {match.group(1)}"
            break

    operating_system = "Unknown OS"
    for pattern, name in (
        (r"Windows NT", "Windows"),
        (r"Android", "Android"),
        (r"iPhone|iPad|iPod", "iOS"),
        (r"Mac OS X", "macOS"),
        (r"Linux", "Linux"),
    ):
        if re.search(pattern, user_agent, re.IGNORECASE):
            operating_system = name
            break

    if re.search(r"iPad|Tablet", user_agent, re.IGNORECASE):
        device = "Tablet"
    elif re.search(r"Mobile|iPhone|Android", user_agent, re.IGNORECASE):
        device = "Mobile device"
    else:
        device = "Desktop"

    return device, browser, operating_system


def create_login_tokens(user, request):
    user_agent = (
        request.META.get("HTTP_X_SESSION_USER_AGENT")
        or request.META.get("HTTP_USER_AGENT", "")
    )[:2000]
    device, browser, operating_system = describe_user_agent(user_agent)
    remote_addr = request.META.get("REMOTE_ADDR")
    try:
        ip = str(ip_address(remote_addr)) if remote_addr else None
    except ValueError:
        ip = None

    with transaction.atomic():
        session = UserSession.objects.create(
            user=user,
            device=device,
            browser=browser,
            operating_system=operating_system,
            user_agent=user_agent,
            ip_address=ip,
        )
        refresh = RefreshToken.for_user(user)
        refresh["session_id"] = str(session.pk)
        session.refresh_jti = refresh["jti"]
        session.save(update_fields=["refresh_jti"])

    return session, refresh


def close_session(session, status):
    if session.status != UserSession.Status.ACTIVE:
        return
    session.status = status
    session.logout_at = timezone.now()
    session.save(update_fields=["status", "logout_at"])

    if session.refresh_jti:
        outstanding = OutstandingToken.objects.filter(jti=session.refresh_jti).first()
        if outstanding:
            BlacklistedToken.objects.get_or_create(token=outstanding)


def session_from_refresh_token(refresh):
    session_id = refresh.get("session_id")
    if not session_id:
        return None
    return UserSession.objects.filter(
        pk=session_id,
        user_id=refresh.get("user_id"),
    ).first()
