from django.contrib import admin

from .models import AppSetting, AuditLog

admin.site.register(AppSetting)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "entity", "entity_id")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
