# TKS Services - Mobile Valeting & Detailing

Professional mobile valeting website with:
- Online booking system backed by FastAPI with SQLite locally and Postgres/Supabase in production
- Admin dashboard for creating, editing, rescheduling, completing, restoring, and cancelling bookings
- Subscription management for customer details, future visit generation, and plan cancellation
- Working-hours and blocked-slot management with clash checks
- Lightweight customer history from booking records
- iPhone Calendar sync through a private `.ics` subscription feed
- Monthly subscription onboarding flow
- Add-on cart system
- Premium design with dark theme + gold accents
- Fully responsive

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ADMIN_TOKEN="choose-a-secret-token" CALENDAR_TOKEN="choose-a-calendar-token" python api_server.py
```

Open:

- Website: `http://localhost:8000`
- Admin: `http://localhost:8000/admin`

The booking database is stored at `data/bookings.sqlite3` by default. Override it with `TKS_DATA_DIR` or `TKS_DB_PATH`.

For production, set a Postgres connection string with one of:

- `DATABASE_URL`
- `POSTGRES_URL`
- `SUPABASE_DB_URL`

When a Postgres URL is present, the app creates and uses the production Postgres tables automatically. SQLite is only the local fallback.

## Admin and calendar sync

Set these environment variables before deployment:

- `ADMIN_TOKEN`: password/token used by `/admin`.
- `CALENDAR_TOKEN`: private token for the iPhone calendar feed.
- `DATABASE_URL` or `POSTGRES_URL`: Vercel Marketplace Postgres/Neon connection string.
- `SUPABASE_DB_URL`: Supabase Postgres connection string if using Supabase instead.
- `BUSINESS_EMAIL`: defaults to `Tksservices1@outlook.com`.
- `TKS_TIMEZONE`: defaults to `Europe/London`.

In `/admin`, copy the calendar feed URL. On iPhone, go to Settings, Calendar, Accounts, Add Account, Other, Add Subscribed Calendar, then paste the feed URL. The feed includes confirmed bookings, generated subscription visits, and blocked slots.

## Vercel

Vercel detects the FastAPI app through `app.py`, which imports the main `api_server.app` instance. Add `ADMIN_TOKEN`, `CALENDAR_TOKEN`, `BUSINESS_EMAIL`, and a Postgres connection string in the Vercel project environment variables before using the admin dashboard.

For Supabase, use the transaction pooler connection string for serverless deployments. The app disables prepared statements for Postgres connections so it can work with pooled Supabase connections.
