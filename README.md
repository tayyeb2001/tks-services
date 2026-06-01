# TKS Services - Mobile Valeting & Detailing

Professional mobile valeting website with:
- Online booking system backed by FastAPI and SQLite
- Admin dashboard for cancellations, blocked slots, working hours, and subscriptions
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

## Admin and calendar sync

Set these environment variables before deployment:

- `ADMIN_TOKEN`: password/token used by `/admin`.
- `CALENDAR_TOKEN`: private token for the iPhone calendar feed.
- `BUSINESS_EMAIL`: defaults to `Tksservices1@outlook.com`.
- `TKS_TIMEZONE`: defaults to `Europe/London`.

In `/admin`, copy the calendar feed URL. On iPhone, go to Settings, Calendar, Accounts, Add Account, Other, Add Subscribed Calendar, then paste the feed URL. The feed includes confirmed bookings, generated subscription visits, and blocked slots.

## Vercel

Vercel detects the FastAPI app through `app.py`, which imports the main `api_server.app` instance. Add `ADMIN_TOKEN`, `CALENDAR_TOKEN`, and `BUSINESS_EMAIL` in the Vercel project environment variables before using the admin dashboard.
