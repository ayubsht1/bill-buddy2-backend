from django.urls import path
from .views import (RegisterView, LoginView, EmailVerifyView, PasswordResetRequestView, PasswordResetConfirmView,
ResendVerificationEmailView, LogoutView, GoogleLoginView, TokenRefreshView)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('email-verify/', EmailVerifyView.as_view(), name='email-verify'),
    path('password-reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('reset-password/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('resend-verification/', ResendVerificationEmailView.as_view(), name='resend-verification'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path("social-login/", GoogleLoginView.as_view(), name="social-login"),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
]