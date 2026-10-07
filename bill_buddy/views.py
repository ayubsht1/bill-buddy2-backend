from rest_framework.views import APIView
# from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from .models import CustomUser, PasswordResetToken, EmailVerificationToken, Friendship
from .utils import send_verification_email, send_password_reset_email
from rest_framework_simplejwt.tokens import RefreshToken, TokenError, AccessToken
from rest_framework.permissions import AllowAny, IsAuthenticated
from .response import custom_response
from .serializers import (
    RegisterSerializer,
    PasswordResetConfirmSerializer,
    UserProfileSerializer,
    FriendshipSerializer,
    FriendRequestSerializer,
    UserSearchSerializer,
    FriendSerializer,
    get_profile_picture_url,
)
from django.core.signing import TimestampSigner, SignatureExpired, BadSignature
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.http import HttpResponseRedirect
from django.conf import settings
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models
from django.core.cache import cache
from hashlib import sha256
from decimal import Decimal
from google.auth.transport.requests import Request as GoogleRequest
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import id_token as google_id_token
from expense.models import Expense, ExpenseShare
from settlement.models import Settlement
from groups.models import Group
User = get_user_model()

class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            # 🌟 Extract the first available error message from the dictionary
            first_error_msg = "Validation failed"
            if serializer.errors:
                # Get the first field name and its list of error strings
                first_field = next(iter(serializer.errors))
                error_list = serializer.errors[first_field]
                if error_list and isinstance(error_list, list):
                    # Clean up the message string
                    first_error_msg = str(error_list[0])
                elif isinstance(error_list, dict):
                    # Fallback for nested serializer structures
                    nested_field = next(iter(error_list))
                    first_error_msg = str(error_list[nested_field][0])

            return custom_response(
                success=False,
                message=first_error_msg,  # 🌟 Sends the exact failing reason (e.g. "This field is required.")
                errors=serializer.errors, # Keeps full dictionary context if needed by frontend UI
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user = serializer.save()
        send_verification_email(user, request)

        return custom_response(
            success=True,
            message="User registered successfully. Please check your email to verify your account, the link will expire in 10 minutes.",
            status_code=status.HTTP_201_CREATED
        )


class EmailVerifyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        token = request.query_params.get('token')
        signer = TimestampSigner()

        try:
            verification_token = EmailVerificationToken.objects.get(token=token)
        except EmailVerificationToken.DoesNotExist:
            return custom_response(
                success=False,
                message="Invalid token.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        if verification_token.used:
            return custom_response(success=False, message="Token already used.")

        if verification_token.is_expired():
            return custom_response(success=False, message="Token expired.")

        try:
            email = signer.unsign(token, max_age=60 * 10)
        except (SignatureExpired, BadSignature):
            return custom_response(
                success=False,
                message="Invalid or expired token.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user = verification_token.user

        if user.email != email:
            return custom_response(success=False, message="Token does not match user.")

        if user.is_active:
            return custom_response(success=True, message="Account already activated.")

        user.is_active = True
        user.save()

        verification_token.used = True
        verification_token.save()

        return HttpResponseRedirect(f"{settings.FRONTEND_URL}/auth/emailVerified")

class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return custom_response(
                success=False,
                message="Email and password required",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        user = authenticate(request, email=email, password=password)

        if user is None:
            return custom_response(
                success=False,
                message="Invalid credentials",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        if not user.is_active:
            return custom_response(
                success=False,
                message="Account not activated. Please verify your email.",
                 status_code=status.HTTP_401_UNAUTHORIZED,
                data={
                "is_active": user.is_active
            }
            )

        refresh = RefreshToken.for_user(user)

        return custom_response(
            success=True,
            message="Login successful",
            data={
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "has_password": user.has_usable_password(),
                    "profile_picture": get_profile_picture_url(request, user.profile_picture),
                }
            },
        )

def generate_safe_username(email):
    base = slugify(email.split("@")[0]) or "user"
    username = base
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base}{counter}"
        counter += 1
    return username

class GoogleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not settings.GOOGLE_CLIENT_ID:
            return custom_response(
                success=False,
                message="Google login is not configured.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        credential = request.data.get("id_token")
        if not isinstance(credential, str) or not credential:
            return custom_response(
                success=False,
                message="A Google ID token is required.",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        try:
            claims = google_id_token.verify_oauth2_token(
                credential,
                GoogleRequest(),
                settings.GOOGLE_CLIENT_ID
            )
        except ValueError:
            return custom_response(
                success=False,
                message="Invalid or expired Google ID token.",
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        except GoogleAuthError:
            return custom_response(
                success=False,
                message="Google identity verification is temporarily unavailable.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        email = claims.get("email")
        if not email or claims.get("email_verified") is not True:
            return custom_response(
                success=False,
                message="Google must provide a verified email address.",
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        picture_url = claims.get("picture")
        first_name = claims.get("given_name")
        last_name = claims.get("family_name")

        try:
            with transaction.atomic():
                user_qs = User.objects.filter(email=email)
                if user_qs.exists():
                    user = user_qs.get()
                    created = False
                    
                    # 🌟 If names are missing on an existing profile, catch them up
                    updated_fields = []
                    if not user.profile_picture and picture_url:
                        user.profile_picture = picture_url
                        updated_fields.append('profile_picture')
                    if not user.first_name and first_name:
                        user.first_name = first_name
                        updated_fields.append('first_name')
                    if not user.last_name and last_name:
                        user.last_name = last_name
                        updated_fields.append('last_name')
                    if not user.is_active:
                        user.is_active = True
                        updated_fields.append('is_active')
                        
                    if updated_fields:
                        user.save(update_fields=updated_fields)
                else:
                    safe_username = generate_safe_username(email)
                    user = User.objects.create(
                        email=email,
                        username=safe_username,
                        first_name=first_name,  # 🌟 Automatically save Google's first name
                        last_name=last_name,    # 🌟 Automatically save Google's last name
                        profile_picture=picture_url,
                        is_active=True,
                    )
                    user.set_unusable_password()
                    user.save()
                    created = True
                    
        except IntegrityError:
            return custom_response(
                success=False,
                message="Database error while creating or retrieving user.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        refresh = RefreshToken.for_user(user)

        msg = "Google account created and logged in." if created else "Google login successful."

        return custom_response(
            success=True,
            message=msg,
            data={
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "profile_picture": get_profile_picture_url(request, user.profile_picture),
                    "has_password": user.has_usable_password(),
                },
            },
        )

class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]
    # Add parsers so Django can read both raw JSON and uploaded file form-data
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        serializer = UserProfileSerializer(request.user, context={'request': request})
        has_password = request.user.has_usable_password()

        return custom_response(
            success=True,
            message="Profile fetched successfully.",
            data={**serializer.data, "has_password": has_password}
        )

    def patch(self, request):
        serializer = UserProfileSerializer(
            request.user, 
            data=request.data, 
            partial=True,
            context={'request': request}  # Passes request context to build absolute image URLs
        )
        
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message="Validation failed",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )
            
        serializer.save()
        has_password = request.user.has_usable_password()
        
        return custom_response(
            success=True,
            message="Profile updated successfully.",
            data={**serializer.data, "has_password": has_password}
        )

    # Route PUT requests to patch so partial profile updates (like updating only a username or picture) work cleanly
    def put(self, request):
        return self.patch(request)
    
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        new_password = request.data.get("new_password")

        if not new_password:
            return custom_response(
                success=False,
                message="New password is required.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 🌟 Cryptographic verify if user has a standard usable credentials hash
        has_password = user.has_usable_password()

        if has_password:
            # Workflow A: Standard User -> Must verify old password match
            old_password = request.data.get("old_password")
            if not old_password:
                return custom_response(
                    success=False,
                    message="Old password is required.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            if not user.check_password(old_password):
                return custom_response(
                    success=False,
                    message="Incorrect old password.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
        else:
            # Workflow B: Google OAuth User -> Creating a password for the very first time
            pass 

        # Enforce local Django authentication engine password complexity rules
        try:
            validate_password(new_password, user)
        except ValidationError as e:
            return custom_response(
                success=False,
                message="Password verification strength rules failed.",
                errors={"new_password": list(e.messages)},
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Commit changes securely
        user.set_password(new_password)
        user.save()

        message = (
            "Password created successfully. You can now use email/password or Google to log in."
            if not has_password
            else "Password updated successfully."
        )

        return custom_response(
            success=True,
            message=message,
            status_code=status.HTTP_200_OK
        )

class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return custom_response(
                success=False,
                message="Email is required",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return custom_response(
                success=True,
                message="If an account exists for that email, a password reset link will be sent."
            )

        send_password_reset_email(user, request)
        return custom_response(
            success=True,
            message="If an account exists for that email, a password reset link will be sent."
        )

class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message="Validation error",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        with transaction.atomic():
            try:
                reset_token = PasswordResetToken.objects.select_for_update().get(token=token)
            except PasswordResetToken.DoesNotExist:
                return custom_response(success=False, message="Invalid token", status_code=400)
            if reset_token.used:
                return custom_response(success=False, message="Token already used", status_code=400)
            if reset_token.is_expired():
                return custom_response(success=False, message="Token expired", status_code=400)

            user = reset_token.user
            try:
                validate_password(new_password, user)
            except ValidationError as exc:
                return custom_response(
                    success=False,
                    message="Password does not meet security requirements.",
                    errors={"new_password": list(exc.messages)},
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            user.set_password(new_password)
            user.save(update_fields=['password'])
            reset_token.used = True
            reset_token.save(update_fields=['used'])

        return custom_response(success=True, message="Password reset successful")

class ResendVerificationEmailView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')

        if not email:
            return custom_response(
                success=False,
                message="Email is required",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return custom_response(success=True, message="If the account needs verification, an email will be sent.")

        if user.is_active:
            return custom_response(success=True, message="If the account needs verification, an email will be sent.")

        send_verification_email(user, request)
        return custom_response(
            success=True,
            message="If the account needs verification, an email will be sent."
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return custom_response(
                success=False,
                message="Refresh token is required.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            token = RefreshToken(refresh_token)
            if str(token.get("user_id")) != str(request.user.id):
                return custom_response(
                    success=False,
                    message="Refresh token does not belong to the authenticated user.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            token.blacklist()

            return custom_response(
                success=True,
                message="Logout successful."
            )

        except TokenError:
            return custom_response(
                success=False,
                message="Invalid or expired refresh token.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

class TokenRefreshView(APIView):
    """
    Takes a valid refresh type JSON web token and returns a fresh, 
    short-lived access token to continue hitting authenticated routes.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        
        if not refresh_token:
            return custom_response(
                success=False,
                message="Refresh token is required.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Load and validate the provided refresh token token string
            refresh = RefreshToken(refresh_token)
            if not User.objects.filter(
                id=refresh.get("user_id"), is_active=True
            ).exists():
                return custom_response(
                    success=False,
                    message="The account for this refresh token is unavailable.",
                    status_code=status.HTTP_401_UNAUTHORIZED
                )
            
            # Generate a clean new access token rotation payload
            data = {
                "access": str(refresh.access_token),
                "refresh": str(refresh)  # Included if token rotation settings are active in settings.py
            }
            
            return custom_response(
                success=True,
                message="Token refreshed successfully.",
                data=data
            )

        except TokenError:
            return custom_response(
                success=False,
                message="Token is invalid or has expired.",
                status_code=status.HTTP_401_UNAUTHORIZED
            )

class VerifyTokenView(APIView):
    """
    Verify token signature, expiry, and current account status.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        token_str = request.data.get("token")
        
        if not token_str:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token_str = auth_header.split(" ")[1]

        if not token_str:
            return custom_response(
                success=False,
                message="Access token is required.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            access_token = AccessToken(token_str)
            if not User.objects.filter(
                id=access_token.get("user_id"), is_active=True
            ).exists():
                return custom_response(
                    success=False,
                    message="The account for this access token is unavailable.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    data={"is_valid": False}
                )

            return custom_response(
                success=True,
                message="Token is valid.",
                data={"is_valid": True}
            )

        except TokenError:
            return custom_response(
                success=False,
                message="Token is invalid or expired.",
                status_code=status.HTTP_401_UNAUTHORIZED,
                data={"is_valid": False}
            )


# ==================== FRIENDSHIP VIEWS ====================

class UserSearchView(APIView):
    """Search for users by username or email"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        query = request.query_params.get('q', '').strip()
        
        if not query or len(query) < 2:
            return custom_response(
                success=True,
                message="Query too short",
                data=[]
            )
        
        cache_key = (
            f'user-search:{request.user.id}:'
            f'{sha256(query.encode("utf-8")).hexdigest()}'
        )
        data = cache.get(cache_key)
        if data is None:
            users = CustomUser.objects.filter(
                models.Q(username__icontains=query) | models.Q(email__icontains=query)
            ).filter(is_active=True).exclude(id=request.user.id).order_by('username')[:20]
            data = UserSearchSerializer(users, many=True).data
            cache.set(cache_key, data, timeout=30)

        return custom_response(
            success=True,
            message="Users found",
            data=data
        )


class SendFriendRequestView(APIView):
    """Send a friend request to another user"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        serializer = FriendRequestSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message="Validation error",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        target_user = CustomUser.objects.get(username__iexact=serializer.validated_data['username'], is_active=True)
        try:
            with transaction.atomic():
                list(
                    CustomUser.objects.select_for_update()
                    .filter(id__in=sorted((request.user.id, target_user.id)))
                    .order_by('id')
                    .values_list('id', flat=True)
                )
                duplicate = Friendship.objects.filter(
                    models.Q(from_user=request.user, to_user=target_user)
                    | models.Q(from_user=target_user, to_user=request.user)
                ).first()
                if duplicate:
                    return custom_response(
                        success=False,
                        message="A friendship or friend request already exists.",
                        status_code=status.HTTP_400_BAD_REQUEST
                    )
                friendship = Friendship.objects.create(
                    from_user=request.user,
                    to_user=target_user,
                    status=Friendship.Status.PENDING
                )
        except IntegrityError:
            return custom_response(
                success=False,
                message="A friendship or friend request already exists.",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        return custom_response(
            success=True,
            message="Friend request sent successfully",
            data=FriendshipSerializer(friendship).data,
            status_code=status.HTTP_201_CREATED
        )


class FriendRequestListView(APIView):
    """List pending friend requests (sent and received)"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        # Received requests
        received = Friendship.objects.filter(
            to_user=request.user,
            status=Friendship.Status.PENDING
        ).select_related('from_user')
        
        # Sent requests
        sent = Friendship.objects.filter(
            from_user=request.user,
            status=Friendship.Status.PENDING
        ).select_related('to_user')
        
        return custom_response(
            success=True,
            message="Friend requests retrieved",
            data={
                'received': FriendshipSerializer(received, many=True).data,
                'sent': FriendshipSerializer(sent, many=True).data
            }
        )


class AcceptFriendRequestView(APIView):
    """Accept a friend request"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, friendship_id):
        with transaction.atomic():
            friendship = get_object_or_404(Friendship.objects.select_for_update(), id=friendship_id)
            if friendship.to_user != request.user:
                return custom_response(
                    success=False,
                    message="You are not authorized to accept this request",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            if friendship.status != Friendship.Status.PENDING:
                return custom_response(
                    success=False,
                    message="This request is no longer pending",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            friendship.status = Friendship.Status.ACCEPTED
            friendship.save(update_fields=['status'])
        
        return custom_response(
            success=True,
            message="Friend request accepted",
            data=FriendshipSerializer(friendship).data
        )


class RejectFriendRequestView(APIView):
    """Reject a friend request"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, friendship_id):
        with transaction.atomic():
            friendship = get_object_or_404(Friendship.objects.select_for_update(), id=friendship_id)
            if friendship.to_user != request.user:
                return custom_response(
                    success=False,
                    message="You are not authorized to reject this request",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            if friendship.status != Friendship.Status.PENDING:
                return custom_response(
                    success=False,
                    message="This request is no longer pending",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            friendship.delete()
        
        return custom_response(
            success=True,
            message="Friend request rejected"
        )


class CancelFriendRequestView(APIView):
    """Cancel a sent friend request"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, friendship_id):
        with transaction.atomic():
            friendship = get_object_or_404(Friendship.objects.select_for_update(), id=friendship_id)
            if friendship.from_user != request.user:
                return custom_response(
                    success=False,
                    message="You are not authorized to cancel this request",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            if friendship.status != Friendship.Status.PENDING:
                return custom_response(
                    success=False,
                    message="This request is no longer pending",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            friendship.delete()
        
        return custom_response(
            success=True,
            message="Friend request cancelled"
        )


class FriendListView(APIView):
    """List all friends with balance information"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        # Get all accepted friendships where user is either from_user or to_user
        friendships = Friendship.objects.filter(
            models.Q(from_user=request.user) | models.Q(to_user=request.user),
            status=Friendship.Status.ACCEPTED
        ).select_related('from_user', 'to_user')
        
        # Extract friend user objects
        friend_ids = []
        for f in friendships:
            if f.from_user == request.user:
                friend_ids.append(f.to_user.id)
            else:
                friend_ids.append(f.from_user.id)
        
        friends = CustomUser.objects.filter(id__in=friend_ids)
        
        # Calculate balances with each friend
        balances = self._calculate_friend_balances(request.user, friend_ids)
        
        serializer = FriendSerializer(friends, many=True, context={'balances': balances})
        
        return custom_response(
            success=True,
            message="Friends retrieved successfully",
            data=serializer.data
        )
    
    def _calculate_friend_balances(self, user, friend_ids):
        """Calculate direct shared-expense and settlement balances with each friend."""
        user_group_ids = Group.objects.filter(members=user).values_list('id', flat=True)
        shared_group_ids = set(
            Group.objects.filter(id__in=user_group_ids, members__id__in=friend_ids)
            .values_list('id', flat=True)
        )
        balances = {}

        for friend_id in friend_ids:
            owed_to_user = ExpenseShare.objects.filter(
                expense__group_id__in=shared_group_ids,
                expense__paid_by=user,
                user_id=friend_id
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            owed_by_user = ExpenseShare.objects.filter(
                expense__group_id__in=shared_group_ids,
                expense__paid_by_id=friend_id,
                user=user
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            paid_to_friend = Settlement.objects.filter(
                group_id__in=shared_group_ids, paid_by=user, paid_to_id=friend_id
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            paid_to_user = Settlement.objects.filter(
                group_id__in=shared_group_ids, paid_by_id=friend_id, paid_to=user
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            balance = owed_to_user - owed_by_user + paid_to_user - paid_to_friend
            balances[friend_id] = float(balance.quantize(Decimal('0.01')))

        return balances


class RemoveFriendView(APIView):
    """Remove a friend (unfriend)"""
    permission_classes = [IsAuthenticated]
    
    def delete(self, request, friend_id):
        # Find the friendship
        friendship = Friendship.objects.filter(
            models.Q(from_user=request.user, to_user_id=friend_id) |
            models.Q(from_user_id=friend_id, to_user=request.user),
            status=Friendship.Status.ACCEPTED
        ).first()
        
        if not friendship:
            return custom_response(
                success=False,
                message="Friendship not found",
                status_code=status.HTTP_404_NOT_FOUND
            )
        
        friendship.delete()
        
        return custom_response(
            success=True,
            message="Friend removed successfully"
        )


class FriendDetailView(APIView):
    """Get details of a specific friend including shared expenses"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, friend_id):
        # Verify they are friends
        friendship = Friendship.objects.filter(
            models.Q(from_user=request.user, to_user_id=friend_id) |
            models.Q(from_user_id=friend_id, to_user=request.user),
            status=Friendship.Status.ACCEPTED
        ).first()
        
        if not friendship:
            return custom_response(
                success=False,
                message="Not friends with this user",
                status_code=status.HTTP_404_NOT_FOUND
            )
        
        friend = CustomUser.objects.get(id=friend_id)
        
        # Get shared groups
        shared_groups = Group.objects.filter(
            members=request.user
        ).filter(members=friend)
        
        # Get shared expenses
        shared_expenses = Expense.objects.filter(
            group__in=shared_groups
        ).filter(
            models.Q(paid_by=request.user) | models.Q(paid_by=friend)
        ).select_related('paid_by', 'group').order_by('-date')[:50]
        
        # Calculate balance
        balances = self._calculate_friend_balances(request.user, [friend_id])
        balance = balances.get(friend_id, 0)
        
        friend_data = UserSearchSerializer(friend).data
        friend_data['balance'] = balance
        friend_data['status'] = 'owed' if balance > 0 else ('owe' if balance < 0 else 'settled')
        friend_data['shared_groups'] = [{'id': g.id, 'name': g.name} for g in shared_groups]
        friend_data['shared_expenses'] = [
            {
                'id': e.id,
                'description': e.description,
                'amount': float(e.amount),
                'paid_by': e.paid_by.username,
                'group': e.group.name,
                'date': e.date.isoformat()
            }
            for e in shared_expenses
        ]
        
        return custom_response(
            success=True,
            message="Friend details retrieved",
            data=friend_data
        )
    
    def _calculate_friend_balances(self, user, friend_ids):
        """Calculate net balance between user and each friend across all shared groups"""
        balances = {}
        
        # Get all groups where user is a member
        user_groups = Group.objects.filter(members=user)
        
        for friend_id in friend_ids:
            net_balance = Decimal('0.00')
            
            # For each group, calculate balance between user and friend
            for group in user_groups:
                if not group.members.filter(id=friend_id).exists():
                    continue
                
                # User paid for expenses in this group
                paid_by_user = Expense.objects.filter(
                    group=group, paid_by=user
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                
                # User's share of expenses in this group
                user_shares = ExpenseShare.objects.filter(
                    expense__group=group, user=user
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                
                # Friend paid for expenses in this group
                paid_by_friend = Expense.objects.filter(
                    group=group, paid_by_id=friend_id
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                
                # Friend's share of expenses in this group
                friend_shares = ExpenseShare.objects.filter(
                    expense__group=group, user_id=friend_id
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                
                # Settlements between user and friend in this group
                settlements_paid = Settlement.objects.filter(
                    group=group, paid_by=user, paid_to_id=friend_id
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                
                settlements_received = Settlement.objects.filter(
                    group=group, paid_by_id=friend_id, paid_to=user
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                
                # Net balance: what friend owes user - what user owes friend
                friend_net = (paid_by_friend - friend_shares + settlements_received - settlements_paid)
                user_net = (paid_by_user - user_shares + settlements_paid - settlements_received)
                
                net_balance += friend_net - user_net
            
            balances[friend_id] = float(net_balance.quantize(Decimal('0.01')))
        
        return balances