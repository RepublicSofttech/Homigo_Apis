# HomeServ – Backend API

Door-to-door services marketplace (Customer · Area Partner · Coworker · Admin).
Django 5 + Django REST Framework + Knox token auth + PostgreSQL.

Built as a fresh project from the ideas in `RepublicSofttech/labour_job` (User/Address/Service/Worker/UserProfile/Knox)
and the BRD/FRD v1.0. The old repo was not changed.

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # edit values
# Option A: PostgreSQL  -> docker compose up -d db     (or your own Postgres, set DATABASE_URL)
# Option B: quick try   -> leave DATABASE_URL empty in .env to use SQLite
python manage.py migrate
python manage.py seed_demo                             # catalogue, plans, offers + demo accounts
python manage.py runserver
```

- API: http://localhost:8000/api/v1/
- Swagger docs: http://localhost:8000/api/docs/
- Django admin: http://localhost:8000/django-admin/

### Demo accounts (created by `seed_demo` — local testing only)

| Role | Phone | Login |
|---|---|---|
| Super Admin | 9000000001 | password `Admin@12345` (change it!) |
| Area Partner (QuickFix, New Delhi) | 9000000002 | OTP |
| Coworkers | 9000000003, 9000000004 | OTP |
| Customer | 9000000005 | OTP |

With `OTP_DEBUG=true` the OTP is returned in the `/auth/otp/send/` response and printed in the server console.
**Set `OTP_DEBUG=false` and plug a real SMS provider in `apps/accounts/services.py::send_sms` before going live.**

### Run tests

```bash
python manage.py test tests
```

### Scheduled job (every minute)

```bash
python manage.py process_sla
```
Handles partner-acceptance SLA (fallback to next nearest partner → auto-cancel + refund), unpaid online bookings,
subscription expiry after grace, and blocking coworkers with expired documents. Run it with cron / Render cron / AWS EventBridge.

## Project structure

```
config/            settings (all secrets from .env), urls
apps/core          base model, AppSetting (admin config), AuditLog, error format, permissions, utils, commands
apps/accounts      User (phone login, user_type, admin_role), OTP, Address (with PATCH), admin users
apps/catalogue     Category → Service → ServiceItem (booking types, price floor/ceiling, radius, warranty...)
apps/partners      AreaPartner, documents, bank (masked), rate card, Coworker (+skills M2M), verification cases
apps/subscriptions Plans, partner subscriptions (grace period, renewal extends)
apps/bookings      Discovery (nearest partner), slots, price preview, booking + action endpoints, dashboards
apps/payments      Payment (mock / Razorpay), refunds, commission ledger, partner wallet, settlements
apps/offers        Coupons (flat / %), funding (platform / partner / shared), validation rules
apps/reviews       Booking-linked reviews, partner reply, admin moderation, rating refresh
apps/support       Tickets + messages
apps/notifications In-app notifications (push/SMS/WhatsApp hook point)
tests/             End-to-end API tests
```

## Business rules implemented

| Rule | Where |
|---|---|
| Nearest live partner shown to customer (exclusivity disabled for now, as decided) | `bookings/services.py::find_partners` |
| BR-P1 go-live gate (approved + active subscription + bank verified + rate card + min approved coworkers + not on holiday) | `partners/models.py::go_live_checklist` |
| BR-C1 coworker linked to one partner; BR-C6 blacklist; BR-C3 identity change → re-verification | `partners/services.py`, `CoworkerViewSet` |
| BR-C2 only approved + active coworkers assignable; no double-booking | `bookings/services.py::assign` |
| BR-B1 status only via action endpoints + status history | `bookings/services.py::TRANSITIONS` |
| BR-B2 server-side price snapshot, add-ons need customer approval | `build_quote`, `addons` actions |
| BR-B3 start / completion OTP (customer sees OTP, partner/coworker never do) | `BookingSerializer` |
| BR-B4 SLA → fallback partner (fallback radius) → cancel + refund | `reject_or_fallback`, `process_sla` |
| BR-F1 commission on service value after partner-funded discount, materials excluded; plan commission % | `payments/services.py::post_completion` |
| Cash jobs → commission debited from partner wallet; online → net credited; settlements | `payments/services.py` |
| BR-S1 subscription check with grace | `PartnerSubscription.current_for` |
| BR-R1 review only after completion, once, inside window | `reviews/views.py` |
| Aadhaar/PAN/bank stored masked (last 4) + hash only | serializers in `partners` |
| One error format `{code, message, field_errors}` | `core/exceptions.py` |

## Main API groups (`/api/v1/`)

- **Auth/profile**: `auth/otp/send/`, `auth/otp/verify/`, `auth/password-login/` (admin), `auth/logout/`, `me/`, `me/avatar/`, `addresses/`
- **Catalogue (public)**: `categories/`, `services/?q=&category=`, `services/{id}/`, `service-items/`
- **Discovery & booking**: `services/{id}/availability/?lat&lng`, `slots/?partner_id&service_id&date`, `bookings/price-preview/`, `bookings/`
- **Booking actions**: `accept`, `reject`, `assign`, `eligible-coworkers`, `on-the-way`, `arrived`, `start`, `addons`,
  `addons/{id}/approve|reject`, `request-completion`, `complete`, `confirm-completion`, `cash-collected`, `no-show`, `cancel-preview`, `cancel`
- **Partner**: `partner/profile/`, `partner/services/`, `partner/documents/`, `partner/bank-account/`, `partner/submit-verification/`,
  `partner/go-live-status/`, `partner/rate-card/`, `partner/coworkers/` (+`submit|activate|deactivate|release`), `partner/coworker-documents/`,
  `partner/subscriptions/` (+`current`, `cancel`), `partner/dashboard/`, `partner/earnings/`, `partner/offers/`, `partner/reviews/`
- **Coworker**: `coworker/jobs/`
- **Payments**: `payments/initiate/`, `payments/verify/`, `payments/webhook/razorpay/`, `payments/`
- **Offers/Reviews/Support/Notifications**: `offers/`, `coupons/validate/`, `reviews/`, `partners/{id}/reviews/`, `tickets/`, `notifications/`
- **Admin**: `admin/dashboard/`, `admin/verification-cases/` (+`claim|approve|reject|resubmit`), `admin/partners/`, `admin/coworkers/`,
  `admin/users/`, `admin/subscriptions/`, `admin/offers/`, `admin/payments/`, `admin/settlements/` (+`run`, `pay`), `admin/commissions/`,
  `admin/reviews/`, `admin/settings/`, `admin/audit-logs/`; catalogue & plans are writable by admins.

Send the token as `Authorization: Token <token>`.

## Before production (pending)

- Real SMS/WhatsApp provider; Razorpay keys (set `PAYMENT_GATEWAY=razorpay`) and webhook secret.
- Private object storage (S3) with signed URLs for KYC documents (currently local `media/private/`).
- Celery/Redis if `process_sla` needs to run more often than cron allows; FCM push.
- PostGIS when territory exclusivity (BR-T1/T2) is switched back on.
- GST/TDS/TCS treatment to be confirmed with the CA (currently GST only on platform fee and commission).
