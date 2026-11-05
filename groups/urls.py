from django.urls import path
from .views import (
    AddGroupMemberView,
    GroupChatView,
    GroupDetailView,
    GroupEventDetailView,
    GroupEventListCreateView,
    GroupListCreateView,
    JoinGroupView,
    RemoveGroupMemberView,
    TransferOwnershipView,
    UpdateMemberRoleView,
    GroupBalancesView,
)

urlpatterns = [
    path('', GroupListCreateView.as_view(), name='group-list-create'),
    path('<uuid:group_id>/', GroupDetailView.as_view(), name='group-detail'),
    path('join/', JoinGroupView.as_view(), name='join-group'),
    path('<uuid:group_id>/chat/', GroupChatView.as_view(), name='group-chat'),
    path('<uuid:group_id>/events/', GroupEventListCreateView.as_view(), name='group-event-list'),
    path(
        '<uuid:group_id>/events/<uuid:event_id>/',
        GroupEventDetailView.as_view(),
        name='group-event-detail',
    ),
    path('<uuid:group_id>/add-member/', AddGroupMemberView.as_view(), name='group-add-member'),
    path('<uuid:group_id>/members/<uuid:user_id>/', RemoveGroupMemberView.as_view(), name='group-remove-member'),
    path('<uuid:group_id>/members/<uuid:user_id>/role/', UpdateMemberRoleView.as_view(), name='group-update-member-role'),
    path('<uuid:group_id>/transfer-ownership/', TransferOwnershipView.as_view(), name='group-transfer-ownership'),
    path('<uuid:group_id>/balances/', GroupBalancesView.as_view(), name='group-balances'),
]