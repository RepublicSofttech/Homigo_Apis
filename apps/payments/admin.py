from django.contrib import admin

from . import models

for m in [models.Tip, models.Payment, models.Refund, models.PartnerWallet, models.WalletTransaction, models.CommissionLedger, models.Settlement]:
    admin.site.register(m)
