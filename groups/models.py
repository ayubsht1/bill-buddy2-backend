from django.db import models
from django.conf import settings

# Create your models here.

class Group(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='Membership', 
        related_name="groups_joined"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="groups_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Membership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[('admin', 'Admin'), ('member', 'Member')])
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'group')
