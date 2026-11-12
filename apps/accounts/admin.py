from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Address, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("phone", "first_name", "last_name", "user_type", "admin_role", "is_active")
    list_filter = ("user_type", "admin_role", "is_active")
    search_fields = ("phone", "first_name", "last_name", "email")
    ordering = ("-date_joined",)
    fieldsets = BaseUserAdmin.fieldsets + (
        ("HomeServ", {"fields": ("phone", "user_type", "admin_role", "gender", "date_of_birth", "avatar", "language")}),
    )
    add_fieldsets = ((None, {"fields": ("phone", "username", "user_type", "password1", "password2")}),)


admin.site.register(Address)
