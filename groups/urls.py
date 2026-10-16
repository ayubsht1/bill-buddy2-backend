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
)

urlpatterns = [
    path('', GroupListCreateView.as_view(), name='group-list-create'),
    path('<int:group_id>/', GroupDetailView.as_view(), name='group-detail'),
    path('join/', JoinGroupView.as_view(), name='join-group'),
    path('<int:group_id>/chat/', GroupChatView.as_view(), name='group-chat'),
    path('<int:group_id>/events/', GroupEventListCreateView.as_view(), name='group-event-list'),
    path(
        '<int:group_id>/events/<int:event_id>/',
        GroupEventDetailView.as_view(),
        name='group-event-detail',
    ),
    path('<int:group_id>/add-member/', AddGroupMemberView.as_view(), name='group-add-member'),
    path('<int:group_id>/members/<int:user_id>/', RemoveGroupMemberView.as_view(), name='group-remove-member'),
    path('<int:group_id>/members/<int:user_id>/role/', UpdateMemberRoleView.as_view(), name='group-update-member-role'),
    path('<int:group_id>/transfer-ownership/', TransferOwnershipView.as_view(), name='group-transfer-ownership'),
]