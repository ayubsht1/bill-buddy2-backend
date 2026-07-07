from django.urls import path
from .views import GroupListCreateView, GroupDetailView, JoinGroupView, GroupChatView

urlpatterns = [
    path('', GroupListCreateView.as_view(), name='group-list-create'),
    path('<int:group_id>/', GroupDetailView.as_view(), name='group-detail'),
    path('join/', JoinGroupView.as_view(), name='join-group'),
    path('<int:group_id>/chat/', GroupChatView.as_view(), name='group-chat'),
]