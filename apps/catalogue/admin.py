from django.contrib import admin

from .models import Service, ServiceCategory, ServiceItem


class ItemInline(admin.TabularInline):
    model = ServiceItem
    extra = 0


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "radius_km", "is_active")
    list_filter = ("category", "is_active")
    inlines = [ItemInline]


admin.site.register(ServiceCategory)
