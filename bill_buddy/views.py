from rest_framework.views import APIView
# from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from .models import CustomUser,PasswordResetToken, EmailVerificationToken
from .utils import send_verification_email, send_password_reset_email
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework.permissions import IsAuthenticated
from .response import custom_response
from .serializers import RegisterSerializer, PasswordResetConfirmSerializer, UserProfileSerializer
from django.core.signing import TimestampSigner, SignatureExpired, BadSignature
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.conf import settings
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
User = get_user_model()

class RegisterView(APIView):
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
    def post(self, request):
        email = request.data.get("email")
        picture_url = request.data.get("picture") 
        
        # 🌟 Read the first and last name sent from your frontend client payload
        first_name = request.data.get("given_name") # Google maps this as given_name
        last_name = request.data.get("family_name")  # Google maps this as family_name

        if not email:
            return custom_response(
                success=False,
                message="Email is required.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

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
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "profile_picture": user.profile_picture, 
                    "has_password": user.has_usable_password(),
                },
            },
        )

class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]
    # Add parsers so Django can read both raw JSON and uploaded file form-data
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        has_password = request.user.has_usable_password()
        return custom_response(
            success=True,
            message="Profile fetched successfully.",
            data={**serializer.data, "has_password": has_password}
        )

    def put(self, request):
        user = request.user
        
        # 1. Handle direct profile image file upload if present in the request
        if 'picture_file' in request.FILES:
            file = request.FILES['picture_file']
            
            # Create a clean unique path (e.g., media/profile_pics/user_5_avatar.png)
            extension = os.path.splitext(file.name)[1]
            file_path = f"profile_pics/user_{user.id}{extension}"
            
            # Save file via Django storage backend
            saved_path = default_storage.save(file_path, ContentFile(file.read()))
            
            # Generate full or relative media URL 
            user.profile_picture = request.build_absolute_uri(default_storage.url(saved_path))
            user.save()

        # 2. Run standard text field updates (username, names, or a passed Google image URL string)
        serializer = UserProfileSerializer(user, data=request.data, partial=True)
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message="Validation failed",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )
            
        serializer.save()
        return custom_response(
            success=True,
            message="Profile updated successfully.",
            data=serializer.data
        )
    
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
                success=False,
                message="User with this email does not exist",
                status_code=status.HTTP_404_NOT_FOUND
            )

        send_password_reset_email(user, request)
        return custom_response(
            success=True,
            message="Password reset request sent. Please check your email the link will expire in 10 minutes."
        )

class PasswordResetConfirmView(APIView):
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        try:
            reset_token = PasswordResetToken.objects.get(token=token)
        except PasswordResetToken.DoesNotExist:
            return custom_response(success=False, message="Invalid token", status_code=400)

        if reset_token.used:
            return custom_response(success=False, message="Token already used", status_code=400)

        if reset_token.is_expired():
            return custom_response(success=False, message="Token expired", status_code=400)

        user = reset_token.user
        user.set_password(new_password)
        user.save()

        reset_token.used = True
        reset_token.save()

        return custom_response(success=True, message="Password reset successful")

class ResendVerificationEmailView(APIView):
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
                success=False,
                message="User with this email does not exist",
                status_code=status.HTTP_404_NOT_FOUND
            )

        if user.is_active:
            return custom_response(
                success=False,
                message="Account is already verified.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        send_verification_email(user, request)
        return custom_response(
            success=True,
            message="Verification email resent. Please check your inbox the link will expire in 10 minutes."
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