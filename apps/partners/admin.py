from django.contrib import admin

from . import models

for m in [models.AreaPartner, models.PartnerService, models.PartnerDocument, models.PartnerBankAccount,
          models.RateCardItem, models.Coworker, models.CoworkerDocument, models.VerificationCase]:
    admin.site.register(m)
