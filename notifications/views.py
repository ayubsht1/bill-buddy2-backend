from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from bill_buddy.response import custom_response
from .models import Notification, NotificationPreference, Reminder
from .serializers import (
    NotificationPreferenceSerializer,
    NotificationSerializer,
    ReminderSerializer,
)


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Notification.objects.filter(user=request.user)
        paginator = NotificationPagination()
        page = paginator.paginate_queryset(queryset, request)
        data = {
            'results': NotificationSerializer(page, many=True).data,
            'count': paginator.page.paginator.count,
            'unread_count': queryset.filter(read_at__isnull=True).count(),
            'next': paginator.get_next_link(),
            'previous': paginator.get_previous_link(),
        }
        return custom_response(success=True, message='Notifications retrieved.', data=data)


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, notification_id):
        notification = get_object_or_404(
            Notification,
            pk=notification_id,
            user=request.user,
        )
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=['read_at'])
        return custom_response(
            success=True,
            message='Notification marked as read.',
            data=NotificationSerializer(notification).data,
        )


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        updated = Notification.objects.filter(
            user=request.user,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        return custom_response(
            success=True,
            message='Notifications marked as read.',
            data={'updated': updated},
        )


class NotificationPreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        preference, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return custom_response(
            success=True,
            message='Notification preferences retrieved.',
            data=NotificationPreferenceSerializer(preference).data,
        )

    def patch(self, request):
        preference, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(
            preference,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message='Validation error.',
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return custom_response(
            success=True,
            message='Notification preferences updated.',
            data=serializer.data,
        )


class ReminderListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        reminders = Reminder.objects.filter(
            created_by=request.user,
        ).select_related('group', 'expense', 'event', 'target_user')
        return custom_response(
            success=True,
            message='Reminders retrieved.',
            data=ReminderSerializer(reminders, many=True, context={'request': request}).data,
        )

    def post(self, request):
        serializer = ReminderSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message='Validation error.',
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        reminder = serializer.save()
        return custom_response(
            success=True,
            message='Reminder scheduled.',
            data=ReminderSerializer(reminder, context={'request': request}).data,
            status_code=status.HTTP_201_CREATED,
        )


class ReminderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, reminder_id):
        reminder = get_object_or_404(
            Reminder,
            pk=reminder_id,
            created_by=request.user,
            status=Reminder.Status.PENDING,
        )
        serializer = ReminderSerializer(
            reminder,
            data=request.data,
            partial=True,
            context={'request': request},
        )
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message='Validation error.',
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        reminder = serializer.save()
        return custom_response(
            success=True,
            message='Reminder updated.',
            data=ReminderSerializer(reminder, context={'request': request}).data,
        )

    def delete(self, request, reminder_id):
        reminder = get_object_or_404(
            Reminder,
            pk=reminder_id,
            created_by=request.user,
            status=Reminder.Status.PENDING,
        )
        reminder.status = Reminder.Status.CANCELLED
        reminder.save(update_fields=['status'])
        return custom_response(success=True, message='Reminder cancelled.')
