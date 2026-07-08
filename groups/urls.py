from django.urls import path
from .views import GroupListCreateView, GroupDetailView, JoinGroupView, GroupChatView, AddGroupMemberView, RemoveGroupMemberView

urlpatterns = [
    path('', GroupListCreateView.as_view(), name='group-list-create'),
    path('<int:group_id>/', GroupDetailView.as_view(), name='group-detail'),
    path('join/', JoinGroupView.as_view(), name='join-group'),
    path('<int:group_id>/chat/', GroupChatView.as_view(), name='group-chat'),
    path('<int:group_id>/add-member/', AddGroupMemberView.as_view(), name='group-add-member'),
    path('<int:group_id>/members/<int:user_id>/', RemoveGroupMemberView.as_view(), name='group-remove-member'),
]