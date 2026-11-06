from django.urls import path

from .views import (
    NotificationListView,
    NotificationPreferenceView,
    NotificationReadAllView,
    NotificationReadView,
    ReminderDetailView,
    ReminderListCreateView,
)

urlpatterns = [
    path('', NotificationListView.as_view(), name='notification-list'),
    path('read-all/', NotificationReadAllView.as_view(), name='notification-read-all'),
    path('<int:notification_id>/read/', NotificationReadView.as_view(), name='notification-read'),
    path('preferences/', NotificationPreferenceView.as_view(), name='notification-preferences'),
    path('reminders/', ReminderListCreateView.as_view(), name='reminder-list-create'),
    path('reminders/<uuid:reminder_id>/', ReminderDetailView.as_view(), name='reminder-detail'),
]
