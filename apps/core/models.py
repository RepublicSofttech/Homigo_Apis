from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppSetting(models.Model):
    """Admin-editable configuration (BRD section 18). Values are JSON."""

    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField()
    description = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key


class AuditLog(models.Model):
    """Immutable record of sensitive actions (status overrides, verification, refunds...)."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    entity = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=50)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.entity}#{self.entity_id}"
