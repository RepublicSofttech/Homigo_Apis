from django.contrib import admin

from .models import Booking, BookingAssignment, BookingItem, BookingOffer, BookingStatusHistory


class ItemInline(admin.TabularInline):
    model = BookingItem
    extra = 0


class HistoryInline(admin.TabularInline):
    model = BookingStatusHistory
    extra = 0
    readonly_fields = ("from_status", "to_status", "actor", "note", "created_at")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("booking_no", "customer", "partner", "service", "status", "total", "scheduled_start")
    list_filter = ("status", "payment_mode", "service")
    search_fields = ("booking_no", "customer__phone")
    inlines = [ItemInline, HistoryInline]


admin.site.register(BookingAssignment)


@admin.register(BookingOffer)
class BookingOfferAdmin(admin.ModelAdmin):
    list_display = ("booking", "partner", "round", "distance_km", "status", "expires_at")
    list_filter = ("status",)
