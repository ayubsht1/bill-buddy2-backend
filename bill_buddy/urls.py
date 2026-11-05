from django.urls import path
from .views import (RegisterView, LoginView, EmailVerifyView, PasswordResetRequestView, PasswordResetConfirmView,
ResendVerificationEmailView, LogoutView, GoogleLoginView, TokenRefreshView, UserProfileView, ChangePasswordView, VerifyTokenView,
UserSearchView, SendFriendRequestView, FriendRequestListView, AcceptFriendRequestView, RejectFriendRequestView,
CancelFriendRequestView, FriendListView, RemoveFriendView, FriendDetailView, PersonalMessageListCreateView,
PersonalMessageDetailView, AccountDeletionView, ActiveSessionsView, SessionHistoryView,
RevokeSessionView, RevokeOtherSessionsView)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('email-verify/', EmailVerifyView.as_view(), name='email-verify'),
    path('password-reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('reset-password/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('profile/', UserProfileView.as_view(), name='user-profile'),
    path('account/delete/', AccountDeletionView.as_view(), name='account-delete'),
    path('change-password/', ChangePasswordView.as_view(), name='change-password'),
    path('resend-verification/', ResendVerificationEmailView.as_view(), name='resend-verification'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path("google-login/", GoogleLoginView.as_view(), name="social-login"),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('token/verify/', VerifyTokenView.as_view(), name='token-verify'),
    path('sessions/', ActiveSessionsView.as_view(), name='active-sessions'),
    path('sessions/history/', SessionHistoryView.as_view(), name='session-history'),
    path('sessions/<uuid:session_id>/revoke/', RevokeSessionView.as_view(), name='revoke-session'),
    path('sessions/revoke-others/', RevokeOtherSessionsView.as_view(), name='revoke-other-sessions'),
    
    # Friendship URLs
    path('friends/search/', UserSearchView.as_view(), name='user-search'),
    path('friends/requests/', FriendRequestListView.as_view(), name='friend-requests'),
    path('friends/requests/send/', SendFriendRequestView.as_view(), name='send-friend-request'),
    path('friends/requests/<uuid:friendship_id>/accept/', AcceptFriendRequestView.as_view(), name='accept-friend-request'),
    path('friends/requests/<uuid:friendship_id>/reject/', RejectFriendRequestView.as_view(), name='reject-friend-request'),
    path('friends/requests/<uuid:friendship_id>/cancel/', CancelFriendRequestView.as_view(), name='cancel-friend-request'),
    path('friends/', FriendListView.as_view(), name='friend-list'),
    path('friends/<uuid:friend_id>/', FriendDetailView.as_view(), name='friend-detail'),
    path('friends/<uuid:friend_id>/remove/', RemoveFriendView.as_view(), name='remove-friend'),

    path('personal-messages/', PersonalMessageListCreateView.as_view(), name='personal-messages'),
    path('personal-messages/<uuid:message_id>/', PersonalMessageDetailView.as_view(), name='personal-message-detail'),
]