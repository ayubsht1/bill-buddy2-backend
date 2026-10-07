from django.contrib import admin

from .models import GroupEvent, GroupEventBudgetItem

admin.site.register(GroupEvent)
admin.site.register(GroupEventBudgetItem)
