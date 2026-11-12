from django.contrib import admin

from .models import PartnerSubscription, SubscriptionPlan

admin.site.register(SubscriptionPlan)
admin.site.register(PartnerSubscription)
