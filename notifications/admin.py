from django.contrib import admin

from .models import EmailDelivery, Notification, NotificationPreference, Reminder

admin.site.register(Notification)
admin.site.register(NotificationPreference)
admin.site.register(EmailDelivery)
admin.site.register(Reminder)
