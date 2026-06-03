#!/usr/bin/env python3
"""TKS Services booking API with local storage and iPhone calendar sync."""

from __future__ import annotations

import calendar
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path("/tmp/tksservices") if os.getenv("VERCEL") else BASE_DIR / "data"
DATA_DIR = Path(os.getenv("TKS_DATA_DIR", DEFAULT_DATA_DIR))
DB_PATH = Path(os.getenv("TKS_DB_PATH", DATA_DIR / "bookings.sqlite3"))
POSTGRES_DSN = (
    os.getenv("DATABASE_URL")
    or os.getenv("POSTGRES_URL")
    or os.getenv("SUPABASE_DB_URL")
    or ""
)
DB_BACKEND = "postgres" if POSTGRES_DSN else "sqlite"
LOCAL_TZ_NAME = os.getenv("TKS_TIMEZONE", "Europe/London")
LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me")
CALENDAR_TOKEN = os.getenv("CALENDAR_TOKEN", ADMIN_TOKEN)
BUSINESS_EMAIL = os.getenv("BUSINESS_EMAIL", "Tksservices1@outlook.com")

SLOT_INTERVAL = 30
SUBSCRIPTION_VISITS_TO_GENERATE = int(os.getenv("SUBSCRIPTION_VISITS_TO_GENERATE", "12"))

SERVICE_DURATIONS = {
    "ceramic-wash": 30,
    "essential-valet": 60,
    "signature-valet": 120,
    "prestige-valet": 180,
    "machine-polish": 150,
    "steam-clean": 45,
    "engine-bay": 60,
    "motorbike-valet": 45,
    "pet-hair": 20,
    "seats-washed": 30,
    "tar-removal": 20,
    "screen-wash": 10,
    "monthly-subscription": 180,
}

SERVICE_CATALOG = [
    {"id": "ceramic-wash", "name": "Ceramic Wash", "price": 25, "duration": SERVICE_DURATIONS["ceramic-wash"]},
    {"id": "essential-valet", "name": "Essential Valet", "price": 45, "duration": SERVICE_DURATIONS["essential-valet"]},
    {"id": "signature-valet", "name": "Signature Valet", "price": 90, "duration": SERVICE_DURATIONS["signature-valet"]},
    {"id": "prestige-valet", "name": "Prestige Valet", "price": 130, "duration": SERVICE_DURATIONS["prestige-valet"]},
    {"id": "machine-polish", "name": "Machine Polish", "price": 100, "duration": SERVICE_DURATIONS["machine-polish"]},
    {"id": "steam-clean", "name": "Steam Clean", "price": 30, "duration": SERVICE_DURATIONS["steam-clean"]},
    {"id": "engine-bay", "name": "Engine Bay Detail", "price": 30, "duration": SERVICE_DURATIONS["engine-bay"]},
    {"id": "motorbike-valet", "name": "Motorbike Valet", "price": 30, "duration": SERVICE_DURATIONS["motorbike-valet"]},
    {"id": "monthly-subscription", "name": "Monthly Subscription", "price": 99, "duration": SERVICE_DURATIONS["monthly-subscription"]},
]

ADDON_DURATIONS = {
    "machine-polish": 150,
    "steam-clean": 45,
    "engine-bay": 60,
    "motorbike-valet": 45,
    "pet-hair": 20,
    "tar-removal": 20,
}

ADDON_CATALOG = [
    {"id": "machine-polish", "name": "Machine Polish", "price": 100, "duration": ADDON_DURATIONS["machine-polish"]},
    {"id": "steam-clean", "name": "Steam Clean", "price": 30, "duration": ADDON_DURATIONS["steam-clean"]},
    {"id": "engine-bay", "name": "Engine Bay Detail", "price": 30, "duration": ADDON_DURATIONS["engine-bay"]},
    {"id": "motorbike-valet", "name": "Motorbike Valet", "price": 30, "duration": ADDON_DURATIONS["motorbike-valet"]},
    {"id": "pet-hair", "name": "Pet Hair Removal", "price": 15, "duration": ADDON_DURATIONS["pet-hair"]},
    {"id": "tar-removal", "name": "Tar Removal", "price": 10, "duration": ADDON_DURATIONS["tar-removal"]},
]

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_LOOKUP = {name.lower(): index for index, name in enumerate(WEEKDAY_NAMES)}


app = FastAPI(title="TKS Services Booking API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ASSETS_DIR = BASE_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


class BookingRequest(BaseModel):
    type: str = "booking"
    service: str
    service_name: str
    service_price: int = 0
    addons: List[str] = Field(default_factory=list)
    addon_names: List[str] = Field(default_factory=list)
    total_price: int = 0
    date: str
    time: str
    end_time: Optional[str] = None
    name: str
    phone: str
    email: str = ""
    vehicle: str = ""
    notes: str = ""
    location: str = ""
    subscription: Dict[str, Any] = Field(default_factory=dict)


class BookingUpdateRequest(BaseModel):
    service: Optional[str] = None
    service_name: Optional[str] = None
    service_price: Optional[int] = None
    addons: Optional[List[str]] = None
    addon_names: Optional[List[str]] = None
    total_price: Optional[int] = None
    date: Optional[str] = None
    time: Optional[str] = None
    end_time: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    vehicle: Optional[str] = None
    notes: Optional[str] = None
    location: Optional[str] = None


class BlockoutRequest(BaseModel):
    date: str
    start_time: str
    end_time: str
    reason: str = "Unavailable"


class CancelRequest(BaseModel):
    reason: str = ""


class WorkingHourItem(BaseModel):
    weekday: int
    is_open: bool
    start_time: str = "08:00"
    end_time: str = "18:00"


class WorkingHoursRequest(BaseModel):
    hours: List[WorkingHourItem]


class SubscriptionUpdateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    vehicle: Optional[str] = None
    vehicle_reg: Optional[str] = None
    vehicle_colour: Optional[str] = None
    condition: Optional[str] = None
    preferred_day: Optional[str] = None
    preferred_time: Optional[str] = None
    start_date: Optional[str] = None
    visit_time: Optional[str] = None
    visit_end_time: Optional[str] = None
    postcode: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    regenerate_future_visits: bool = False


class SubscriptionVisitRequest(BaseModel):
    date: str = ""
    time: str = ""
    duration_minutes: int = Field(default=120, ge=30, le=480)
    service_name: str = "Monthly Maintenance Valet"
    total_price: int = 40


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_local() -> date_cls:
    return datetime.now(LOCAL_TZ).date()


@contextmanager
def db() -> Iterable[Any]:
    if DB_BACKEND == "postgres":
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Postgres support requires psycopg[binary]. Run pip install -r requirements.txt.") from exc

        dsn = postgres_dsn()
        conn = psycopg.connect(dsn, row_factory=dict_row, prepare_threshold=None)
        wrapped = DatabaseConnection(conn, "postgres")
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        wrapped = DatabaseConnection(conn, "sqlite")
        wrapped.execute("PRAGMA foreign_keys = ON")

    try:
        yield wrapped
        wrapped.commit()
    finally:
        wrapped.close()


def postgres_dsn() -> str:
    dsn = POSTGRES_DSN
    if not dsn:
        return dsn
    lowered = dsn.lower()
    is_local = "localhost" in lowered or "127.0.0.1" in lowered
    if "sslmode=" not in lowered and not is_local:
        separator = "&" if "?" in dsn else "?"
        dsn = f"{dsn}{separator}sslmode=require"
    return dsn


class DatabaseConnection:
    def __init__(self, conn: Any, backend: str) -> None:
        self.conn = conn
        self.backend = backend

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> Any:
        if self.backend == "postgres":
            sql = sql.replace("?", "%s")
        return self.conn.execute(sql, params)

    def executescript(self, script: str) -> None:
        if self.backend == "sqlite":
            self.conn.executescript(script)
            return
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'active',
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                vehicle TEXT NOT NULL DEFAULT '',
                vehicle_reg TEXT NOT NULL DEFAULT '',
                vehicle_colour TEXT NOT NULL DEFAULT '',
                condition TEXT NOT NULL DEFAULT '',
                preferred_day TEXT NOT NULL DEFAULT '',
                preferred_time TEXT NOT NULL DEFAULT '',
                start_date TEXT NOT NULL,
                visit_time TEXT NOT NULL DEFAULT '09:00',
                visit_end_time TEXT NOT NULL DEFAULT '11:00',
                postcode TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'booking',
                status TEXT NOT NULL DEFAULT 'confirmed',
                service TEXT NOT NULL,
                service_name TEXT NOT NULL,
                service_price INTEGER NOT NULL DEFAULT 0,
                addons_json TEXT NOT NULL DEFAULT '[]',
                addon_names_json TEXT NOT NULL DEFAULT '[]',
                total_price INTEGER NOT NULL DEFAULT 0,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                vehicle TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                subscription_id TEXT,
                cancelled_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                calendar_uid TEXT NOT NULL,
                FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_bookings_date_status ON bookings(date, status);
            CREATE INDEX IF NOT EXISTS idx_bookings_subscription ON bookings(subscription_id);

            CREATE TABLE IF NOT EXISTS blockouts (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'Unavailable',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_blockouts_date ON blockouts(date);

            CREATE TABLE IF NOT EXISTS working_hours (
                weekday INTEGER PRIMARY KEY,
                is_open INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL
            );
            """
        )

        count = conn.execute("SELECT COUNT(*) AS count FROM working_hours").fetchone()["count"]
        if count == 0:
            for weekday in range(7):
                is_open = weekday < 6
                conn.execute(
                    """
                    INSERT INTO working_hours (weekday, is_open, start_time, end_time)
                    VALUES (?, ?, ?, ?)
                    """,
                    (weekday, int(is_open), "08:00", "18:00"),
                )


init_db()


def require_admin(
    x_admin_token: str = Header(default=""),
    token: str = Query(default=""),
) -> None:
    supplied = x_admin_token or token
    if not supplied or supplied != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def require_calendar_token(token: str = Query(default="")) -> None:
    if not token or token != CALENDAR_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid calendar token")


def parse_date(value: str) -> date_cls:
    try:
        return date_cls.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}") from exc


def time_to_minutes(value: str) -> int:
    if not value or len(value) != 5 or value[2] != ":":
        raise HTTPException(status_code=400, detail=f"Invalid time: {value}")
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid time: {value}") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise HTTPException(status_code=400, detail=f"Invalid time: {value}")
    return hour * 60 + minute


def minutes_to_time(minutes: int) -> str:
    if minutes < 0 or minutes >= 24 * 60:
        raise HTTPException(status_code=400, detail="Time falls outside the current day")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def validate_time_range(start_time: str, end_time: str) -> Tuple[int, int]:
    start = time_to_minutes(start_time)
    end = time_to_minutes(end_time)
    if end <= start:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    return start, end


def display_time(value: str) -> str:
    minutes = time_to_minutes(value)
    hour = minutes // 60
    minute = minutes % 60
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and end_a > start_b


def service_duration(service: str, addons: Optional[List[str]] = None) -> int:
    duration = SERVICE_DURATIONS.get(service, 60)
    for addon in addons or []:
        duration += ADDON_DURATIONS.get(addon, 0)
    return duration


def service_from_catalog(service_id: str) -> Optional[Dict[str, Any]]:
    return next((service for service in SERVICE_CATALOG if service["id"] == service_id), None)


def addon_names_for(addons: List[str]) -> List[str]:
    names = []
    for addon_id in addons:
        match = next((addon for addon in ADDON_CATALOG if addon["id"] == addon_id), None)
        names.append(match["name"] if match else addon_id)
    return names


def default_service_name(service_id: str) -> str:
    match = service_from_catalog(service_id)
    if match:
        return str(match["name"])
    return service_id.replace("-", " ").title()


def default_service_price(service_id: str) -> int:
    match = service_from_catalog(service_id)
    if match:
        return int(match["price"])
    return 0


def parse_addons_param(addons: str = "") -> List[str]:
    return [item.strip() for item in addons.split(",") if item.strip()]


def row_to_booking(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["addons"] = json.loads(item.pop("addons_json") or "[]")
    item["addon_names"] = json.loads(item.pop("addon_names_json") or "[]")
    item["display_time"] = display_time(item["time"])
    item["display_end_time"] = display_time(item["end_time"])
    item["date_display"] = parse_date(item["date"]).strftime("%a %d %b %Y")
    return item


def row_to_subscription(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["date_display"] = parse_date(item["start_date"]).strftime("%a %d %b %Y")
    item["display_time"] = display_time(item["visit_time"])
    item["display_end_time"] = display_time(item["visit_end_time"])
    return item


def row_to_blockout(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["display_time"] = display_time(item["start_time"])
    item["display_end_time"] = display_time(item["end_time"])
    item["date_display"] = parse_date(item["date"]).strftime("%a %d %b %Y")
    return item


def working_hours_for_date(conn: sqlite3.Connection, date_value: str) -> Optional[sqlite3.Row]:
    weekday = parse_date(date_value).weekday()
    row = conn.execute(
        "SELECT weekday, is_open, start_time, end_time FROM working_hours WHERE weekday = ?",
        (weekday,),
    ).fetchone()
    if not row or not row["is_open"]:
        return None
    return row


def busy_ranges(
    conn: sqlite3.Connection,
    date_value: str,
    ignore_booking_id: str = "",
    ignore_blockout_id: str = "",
) -> List[Dict[str, Any]]:
    ranges: List[Dict[str, Any]] = []
    booking_rows = conn.execute(
        """
        SELECT id, time, end_time, service_name, name, type
        FROM bookings
        WHERE date = ? AND status = 'confirmed' AND id <> ?
        ORDER BY time
        """,
        (date_value, ignore_booking_id),
    ).fetchall()
    for row in booking_rows:
        ranges.append(
            {
                "kind": "booking",
                "id": row["id"],
                "start": time_to_minutes(row["time"]),
                "end": time_to_minutes(row["end_time"]),
                "label": f"{row['service_name']} - {row['name']}",
            }
        )

    blockout_rows = conn.execute(
        """
        SELECT id, start_time, end_time, reason
        FROM blockouts
        WHERE date = ? AND id <> ?
        ORDER BY start_time
        """,
        (date_value, ignore_blockout_id),
    ).fetchall()
    for row in blockout_rows:
        ranges.append(
            {
                "kind": "blockout",
                "id": row["id"],
                "start": time_to_minutes(row["start_time"]),
                "end": time_to_minutes(row["end_time"]),
                "label": row["reason"],
            }
        )
    return ranges


def slot_availability(
    conn: sqlite3.Connection,
    date_value: str,
    start_time: str,
    end_time: str,
    ignore_working_hours: bool = False,
    ignore_booking_id: str = "",
    ignore_blockout_id: str = "",
) -> Tuple[bool, str]:
    parse_date(date_value)
    start, end = validate_time_range(start_time, end_time)

    if not ignore_working_hours:
        hours = working_hours_for_date(conn, date_value)
        if not hours:
            return False, "This day is closed"
        working_start = time_to_minutes(hours["start_time"])
        working_end = time_to_minutes(hours["end_time"])
        if start < working_start or end > working_end:
            return False, "Slot is outside working hours"

    for busy in busy_ranges(conn, date_value, ignore_booking_id, ignore_blockout_id):
        if ranges_overlap(start, end, busy["start"], busy["end"]):
            return False, f"Conflicts with {busy['label']}"
    return True, ""


def available_slots_for_date(
    conn: sqlite3.Connection,
    date_value: str,
    service: str,
    addons: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    parsed_date = parse_date(date_value)
    if parsed_date < today_local():
        return []

    hours = working_hours_for_date(conn, date_value)
    if not hours:
        return []

    duration = service_duration(service, addons)
    working_start = time_to_minutes(hours["start_time"])
    working_end = time_to_minutes(hours["end_time"])
    current = working_start
    slots: List[Dict[str, str]] = []

    now_local = datetime.now(LOCAL_TZ)
    is_today = parsed_date == now_local.date()

    while current + duration <= working_end:
        if not is_today or current > (now_local.hour * 60 + now_local.minute):
            start_time = minutes_to_time(current)
            end_time = minutes_to_time(current + duration)
            available, _ = slot_availability(conn, date_value, start_time, end_time)
            if available:
                slots.append(
                    {
                        "time": start_time,
                        "display": display_time(start_time),
                        "end_time": end_time,
                        "end_display": display_time(end_time),
                    }
                )
        current += SLOT_INTERVAL

    return slots


def insert_booking(
    conn: sqlite3.Connection,
    *,
    booking_type: str,
    service: str,
    service_name: str,
    service_price: int,
    addons: List[str],
    addon_names: List[str],
    total_price: int,
    date_value: str,
    start_time: str,
    end_time: str,
    name: str,
    phone: str,
    email: str = "",
    vehicle: str = "",
    notes: str = "",
    location: str = "",
    subscription_id: Optional[str] = None,
    validate_slot: bool = True,
) -> Dict[str, Any]:
    parse_date(date_value)
    validate_time_range(start_time, end_time)

    if validate_slot:
        available, reason = slot_availability(conn, date_value, start_time, end_time)
        if not available:
            raise HTTPException(status_code=409, detail=reason)

    timestamp = now_iso()
    booking_id = str(uuid.uuid4())
    calendar_uid = f"{booking_id}@tks-services"
    conn.execute(
        """
        INSERT INTO bookings (
            id, type, status, service, service_name, service_price, addons_json,
            addon_names_json, total_price, date, time, end_time, name, phone,
            email, vehicle, notes, location, subscription_id, cancelled_reason,
            created_at, updated_at, calendar_uid
        )
        VALUES (?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
        """,
        (
            booking_id,
            booking_type,
            service,
            service_name,
            service_price,
            json.dumps(addons),
            json.dumps(addon_names),
            total_price,
            date_value,
            start_time,
            end_time,
            name.strip(),
            phone.strip(),
            email.strip(),
            vehicle.strip(),
            notes.strip(),
            location.strip(),
            subscription_id,
            timestamp,
            timestamp,
            calendar_uid,
        ),
    )
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    return row_to_booking(row)


def booking_payload_values(payload: BookingRequest, allow_custom_end: bool = False) -> Dict[str, Any]:
    addons = payload.addons or []
    addon_names = payload.addon_names or addon_names_for(addons)
    service_name = payload.service_name.strip() or default_service_name(payload.service)
    service_price = payload.service_price if payload.service_price else default_service_price(payload.service)
    start_minutes = time_to_minutes(payload.time)
    computed_end_time = minutes_to_time(start_minutes + service_duration(payload.service, addons))
    end_time = payload.end_time if allow_custom_end and payload.end_time else computed_end_time
    validate_time_range(payload.time, end_time)
    total_price = payload.total_price if payload.total_price else service_price
    return {
        "service": payload.service,
        "service_name": service_name,
        "service_price": service_price,
        "addons": addons,
        "addon_names": addon_names,
        "total_price": total_price,
        "date": payload.date,
        "time": payload.time,
        "end_time": end_time,
        "name": payload.name,
        "phone": payload.phone,
        "email": payload.email,
        "vehicle": payload.vehicle,
        "notes": payload.notes,
        "location": payload.location,
    }


def subscription_location(subscription: Dict[str, Any]) -> str:
    return str(
        subscription.get("address")
        or subscription.get("postcode")
        or "Mobile - Customer Location"
    )


def create_subscription_visit(
    conn: sqlite3.Connection,
    subscription: Dict[str, Any],
    *,
    date_value: str,
    start_time: str,
    end_time: str,
    service_name: str = "Monthly Maintenance Valet",
    total_price: int = 40,
    validate_slot: bool = True,
) -> Dict[str, Any]:
    return insert_booking(
        conn,
        booking_type="subscription_visit",
        service="monthly-subscription",
        service_name=service_name,
        service_price=total_price,
        addons=[],
        addon_names=[],
        total_price=total_price,
        date_value=date_value,
        start_time=start_time,
        end_time=end_time,
        name=str(subscription.get("name") or ""),
        phone=str(subscription.get("phone") or ""),
        email=str(subscription.get("email") or ""),
        vehicle=str(subscription.get("vehicle") or ""),
        notes=f"Monthly subscription visit for subscription {subscription['id']}",
        location=subscription_location(subscription),
        subscription_id=str(subscription["id"]),
        validate_slot=validate_slot,
    )


def generate_future_subscription_visits(
    conn: sqlite3.Connection,
    subscription: Dict[str, Any],
    first_target: date_cls,
    count: int = SUBSCRIPTION_VISITS_TO_GENERATE,
) -> List[Dict[str, Any]]:
    preferred_day = str(subscription.get("preferred_day") or "")
    desired_start = str(subscription.get("visit_time") or "09:00")
    visit_start_minutes = time_to_minutes(desired_start)
    visit_end = str(subscription.get("visit_end_time") or "")
    duration = 120
    if visit_end:
        duration = max(30, time_to_minutes(visit_end) - visit_start_minutes)

    generated: List[Dict[str, Any]] = []
    for index in range(count):
        target = add_months(first_target, index)
        target = adjust_to_preferred_weekday(target, preferred_day)
        visit_date, start_time, end_time = find_next_available_slot(
            conn,
            target,
            desired_start,
            duration,
            max_days=14,
        )
        generated.append(
            create_subscription_visit(
                conn,
                subscription,
                date_value=visit_date,
                start_time=start_time,
                end_time=end_time,
                validate_slot=False,
            )
        )
    return generated


def parse_subscription_notes(notes: str) -> Dict[str, str]:
    details: Dict[str, str] = {}
    for line in notes.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized = key.strip().lower()
        if normalized == "reg":
            details["vehicleReg"] = value.strip()
        elif normalized == "colour":
            details["vehicleColour"] = value.strip()
        elif normalized == "condition":
            details["condition"] = value.strip()
        elif normalized == "preferred monthly day":
            details["preferredDay"] = value.strip()
        elif normalized == "preferred time":
            details["preferredTime"] = value.strip()
        elif normalized == "postcode":
            details["postcode"] = value.strip()
        elif normalized == "address":
            details["address"] = value.strip()
        elif normalized == "notes":
            details["notes"] = value.strip()
    return details


def time_window_to_start(value: str) -> str:
    lowered = (value or "").lower()
    if "afternoon" in lowered:
        return "12:00"
    if "evening" in lowered:
        return "16:00"
    return "09:00"


def add_months(start: date_cls, months: int) -> date_cls:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date_cls(year, month, day)


def adjust_to_preferred_weekday(target: date_cls, preferred_day: str) -> date_cls:
    weekday = WEEKDAY_LOOKUP.get((preferred_day or "").lower())
    if weekday is None:
        return target
    delta = (weekday - target.weekday()) % 7
    return target + timedelta(days=delta)


def find_next_available_slot(
    conn: sqlite3.Connection,
    start_date: date_cls,
    desired_start_time: str,
    duration: int,
    max_days: int = 30,
) -> Tuple[str, str, str]:
    desired_start = time_to_minutes(desired_start_time)
    for offset in range(max_days + 1):
        candidate = start_date + timedelta(days=offset)
        candidate_date = candidate.isoformat()
        hours = working_hours_for_date(conn, candidate_date)
        if not hours:
            continue

        working_start = time_to_minutes(hours["start_time"])
        working_end = time_to_minutes(hours["end_time"])
        start_candidates = [desired_start]
        current = working_start
        while current + duration <= working_end:
            if current not in start_candidates:
                start_candidates.append(current)
            current += SLOT_INTERVAL

        for start_minutes in start_candidates:
            if start_minutes < working_start or start_minutes + duration > working_end:
                continue
            start_time = minutes_to_time(start_minutes)
            end_time = minutes_to_time(start_minutes + duration)
            available, _ = slot_availability(conn, candidate_date, start_time, end_time)
            if available:
                return candidate_date, start_time, end_time

    raise HTTPException(status_code=409, detail="No available subscription slot found")


def create_subscription_from_booking(conn: sqlite3.Connection, booking: BookingRequest) -> Dict[str, Any]:
    details = {**parse_subscription_notes(booking.notes), **booking.subscription}
    requested_start_date = parse_date(booking.date)
    preferred_day = str(details.get("preferredDay") or details.get("preferred_day") or "")
    preferred_time = str(details.get("preferredTime") or details.get("preferred_time") or "")
    visit_start = time_window_to_start(preferred_time)
    visit_end = minutes_to_time(time_to_minutes(visit_start) + 120)
    initial_start = booking.time if booking.time else visit_start

    subscription_id = str(uuid.uuid4())
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO subscriptions (
            id, status, name, phone, email, vehicle, vehicle_reg, vehicle_colour,
            condition, preferred_day, preferred_time, start_date, visit_time,
            visit_end_time, postcode, address, notes, created_at, updated_at
        )
        VALUES (?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            subscription_id,
            booking.name.strip(),
            booking.phone.strip(),
            booking.email.strip(),
            booking.vehicle.strip(),
            str(details.get("vehicleReg") or details.get("vehicle_reg") or "").strip(),
            str(details.get("vehicleColour") or details.get("vehicle_colour") or "").strip(),
            str(details.get("condition") or "").strip(),
            preferred_day.strip(),
            preferred_time.strip(),
            requested_start_date.isoformat(),
            visit_start,
            visit_end,
            str(details.get("postcode") or "").strip(),
            str(details.get("address") or "").strip(),
            str(details.get("notes") or booking.notes or "").strip(),
            timestamp,
            timestamp,
        ),
    )

    initial_date, initial_start_time, initial_end_time = find_next_available_slot(
        conn,
        requested_start_date,
        initial_start,
        180,
        max_days=30,
    )
    initial_booking = insert_booking(
        conn,
        booking_type="subscription_initial",
        service="monthly-subscription",
        service_name="Initial Deep Clean - Monthly Subscription",
        service_price=99,
        addons=[],
        addon_names=[],
        total_price=99,
        date_value=initial_date,
        start_time=initial_start_time,
        end_time=initial_end_time,
        name=booking.name,
        phone=booking.phone,
        email=booking.email,
        vehicle=booking.vehicle,
        notes=booking.notes,
        location=str(details.get("address") or details.get("postcode") or "Mobile - Customer Location"),
        subscription_id=subscription_id,
        validate_slot=False,
    )

    generated_visits: List[Dict[str, Any]] = []
    first_visit_anchor = parse_date(initial_date)
    for index in range(1, SUBSCRIPTION_VISITS_TO_GENERATE + 1):
        target = add_months(first_visit_anchor, index)
        target = adjust_to_preferred_weekday(target, preferred_day)
        visit_date, start_time, end_time = find_next_available_slot(
            conn,
            target,
            visit_start,
            120,
            max_days=14,
        )
        generated_visits.append(
            insert_booking(
                conn,
                booking_type="subscription_visit",
                service="monthly-subscription",
                service_name="Monthly Maintenance Valet",
                service_price=40,
                addons=[],
                addon_names=[],
                total_price=40,
                date_value=visit_date,
                start_time=start_time,
                end_time=end_time,
                name=booking.name,
                phone=booking.phone,
                email=booking.email,
                vehicle=booking.vehicle,
                notes=f"Generated monthly subscription visit for subscription {subscription_id}",
                location=str(details.get("address") or details.get("postcode") or "Mobile - Customer Location"),
                subscription_id=subscription_id,
                validate_slot=False,
            )
        )

    subscription_row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
    return {
        "success": True,
        "message": "Subscription created",
        "subscription": row_to_subscription(subscription_row),
        "booking": initial_booking,
        "generated_visits": generated_visits,
    }


def local_dt(date_value: str, time_value: str) -> datetime:
    parsed_date = parse_date(date_value)
    minutes = time_to_minutes(time_value)
    return datetime(
        parsed_date.year,
        parsed_date.month,
        parsed_date.day,
        minutes // 60,
        minutes % 60,
        tzinfo=LOCAL_TZ,
    )


def ics_datetime(date_value: str, time_value: str) -> str:
    return local_dt(date_value, time_value).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ics_escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def booking_description(booking: Dict[str, Any]) -> str:
    lines = [
        f"Service: {booking['service_name']}",
        f"Price: GBP {booking['total_price']}",
        f"Customer: {booking['name']}",
        f"Phone: {booking['phone']}",
    ]
    if booking.get("email"):
        lines.append(f"Email: {booking['email']}")
    if booking.get("vehicle"):
        lines.append(f"Vehicle: {booking['vehicle']}")
    if booking.get("addon_names"):
        lines.append("Add-ons: " + ", ".join(booking["addon_names"]))
    if booking.get("notes"):
        lines.append("")
        lines.append(booking["notes"])
    return "\n".join(lines)


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.head("/")
async def head_index() -> Response:
    return Response(status_code=200)


@app.get("/admin")
async def serve_admin() -> FileResponse:
    return FileResponse(BASE_DIR / "admin.html")


@app.head("/admin")
async def head_admin() -> Response:
    return Response(status_code=200)


@app.get("/api/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "timezone": LOCAL_TZ_NAME, "database": DB_BACKEND}


@app.get("/api/services")
async def services() -> Dict[str, Any]:
    return {
        "services": SERVICE_CATALOG,
        "addons": ADDON_CATALOG,
        "slot_interval": SLOT_INTERVAL,
    }


@app.get("/api/available-dates")
async def get_available_dates(
    service: str = "essential-valet",
    addons: str = "",
    days: int = Query(default=14, ge=1, le=60),
) -> Dict[str, Any]:
    addon_list = parse_addons_param(addons)
    dates = []
    with db() as conn:
        start = today_local()
        for offset in range(1, days + 1):
            candidate = start + timedelta(days=offset)
            date_value = candidate.isoformat()
            if not available_slots_for_date(conn, date_value, service, addon_list):
                continue
            dates.append(
                {
                    "date": date_value,
                    "day_name": WEEKDAY_NAMES[candidate.weekday()],
                    "display": candidate.strftime("%d %B"),
                    "short": candidate.strftime("%d %b"),
                }
            )

    return {
        "dates": dates,
        "service": service,
        "duration": service_duration(service, addon_list),
    }


@app.get("/api/available-slots")
async def get_available_slots_endpoint(
    date: str,
    service: str = "essential-valet",
    addons: str = "",
) -> Dict[str, Any]:
    addon_list = parse_addons_param(addons)
    with db() as conn:
        slots = available_slots_for_date(conn, date, service, addon_list)
    return {
        "date": date,
        "service": service,
        "duration": service_duration(service, addon_list),
        "slots": slots,
    }


@app.post("/api/book")
async def create_booking(booking: BookingRequest) -> Dict[str, Any]:
    if not booking.name.strip() or not booking.phone.strip():
        raise HTTPException(status_code=400, detail="Name and phone are required")

    with db() as conn:
        if booking.type == "subscription" or booking.service == "monthly-subscription":
            return create_subscription_from_booking(conn, booking)

        values = booking_payload_values(booking)
        stored_booking = insert_booking(
            conn,
            booking_type="booking",
            service=values["service"],
            service_name=values["service_name"],
            service_price=values["service_price"],
            addons=values["addons"],
            addon_names=values["addon_names"],
            total_price=values["total_price"],
            date_value=values["date"],
            start_time=values["time"],
            end_time=values["end_time"],
            name=values["name"],
            phone=values["phone"],
            email=values["email"],
            vehicle=values["vehicle"],
            notes=values["notes"],
            location=values["location"] or "Mobile - Customer Location",
            validate_slot=True,
        )

    return {
        "success": True,
        "message": f"Booking confirmed for {booking.service_name} on {booking.date} at {booking.time}",
        "booking": stored_booking,
    }


@app.get("/api/admin/state", dependencies=[Depends(require_admin)])
async def admin_state(request: Request) -> Dict[str, Any]:
    today = today_local()
    start_date = (today - timedelta(days=30)).isoformat()
    today_value = today.isoformat()
    with db() as conn:
        bookings = [
            row_to_booking(row)
            for row in conn.execute(
                """
                SELECT * FROM bookings
                WHERE date >= ?
                ORDER BY date, time
                LIMIT 500
                """,
                (start_date,),
            ).fetchall()
        ]
        subscriptions = [
            row_to_subscription(row)
            for row in conn.execute(
                "SELECT * FROM subscriptions ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        ]
        blockouts = [
            row_to_blockout(row)
            for row in conn.execute(
                """
                SELECT * FROM blockouts
                WHERE date >= ?
                ORDER BY date, start_time
                LIMIT 200
                """,
                (start_date,),
            ).fetchall()
        ]
        working_hours = [
            dict(row)
            for row in conn.execute(
                "SELECT weekday, is_open, start_time, end_time FROM working_hours ORDER BY weekday"
            ).fetchall()
        ]
        today_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM bookings
            WHERE date = ? AND status = 'confirmed'
            """,
            (today_value,),
        ).fetchone()["count"]
        upcoming_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM bookings
            WHERE date >= ? AND status = 'confirmed'
            """,
            (today_value,),
        ).fetchone()["count"]
        customer_rows = conn.execute(
            """
            SELECT name, phone, email, vehicle, date, total_price, status
            FROM bookings
            ORDER BY date DESC, time DESC
            LIMIT 500
            """
        ).fetchall()

    customers_by_key: Dict[str, Dict[str, Any]] = {}
    for row in customer_rows:
        item = dict(row)
        key = (item.get("phone") or item.get("email") or item.get("name") or "").strip().lower()
        if not key:
            continue
        customer = customers_by_key.setdefault(
            key,
            {
                "name": item.get("name") or "",
                "phone": item.get("phone") or "",
                "email": item.get("email") or "",
                "vehicle": item.get("vehicle") or "",
                "last_booking_date": item.get("date") or "",
                "booking_count": 0,
                "total_spend": 0,
            },
        )
        if not customer["last_booking_date"] or str(item.get("date") or "") > customer["last_booking_date"]:
            customer["last_booking_date"] = item.get("date") or ""
            customer["vehicle"] = item.get("vehicle") or customer["vehicle"]
        if item.get("status") in ("confirmed", "completed"):
            customer["booking_count"] += 1
            customer["total_spend"] += int(item.get("total_price") or 0)

    customers = sorted(
        customers_by_key.values(),
        key=lambda item: item["last_booking_date"],
        reverse=True,
    )[:100]

    return {
        "bookings": bookings,
        "subscriptions": subscriptions,
        "blockouts": blockouts,
        "working_hours": working_hours,
        "customers": customers,
        "stats": {
            "today_bookings": int(today_count),
            "upcoming_bookings": int(upcoming_count),
            "active_subscriptions": len([item for item in subscriptions if item["status"] == "active"]),
            "upcoming_blockouts": len(blockouts),
        },
        "catalog": {
            "services": SERVICE_CATALOG,
            "addons": ADDON_CATALOG,
        },
        "settings": {
            "timezone": LOCAL_TZ_NAME,
            "database": DB_BACKEND,
            "business_email": BUSINESS_EMAIL,
            "calendar_feed_path": f"/calendar/tks-services.ics?token={CALENDAR_TOKEN}",
            "default_admin_token": ADMIN_TOKEN == "change-me",
            "base_url": str(request.base_url).rstrip("/"),
        },
    }


@app.patch("/api/admin/bookings/{booking_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_booking(booking_id: str, payload: CancelRequest) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Booking not found")
        conn.execute(
            """
            UPDATE bookings
            SET status = 'cancelled', cancelled_reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.reason.strip(), now_iso(), booking_id),
        )
        updated = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    return {"success": True, "booking": row_to_booking(updated)}


@app.post("/api/admin/bookings", dependencies=[Depends(require_admin)])
async def create_admin_booking(payload: BookingRequest) -> Dict[str, Any]:
    if not payload.name.strip() or not payload.phone.strip():
        raise HTTPException(status_code=400, detail="Name and phone are required")

    with db() as conn:
        if payload.type == "subscription" or payload.service == "monthly-subscription":
            return create_subscription_from_booking(conn, payload)

        values = booking_payload_values(payload, allow_custom_end=True)
        stored_booking = insert_booking(
            conn,
            booking_type="booking",
            service=values["service"],
            service_name=values["service_name"],
            service_price=values["service_price"],
            addons=values["addons"],
            addon_names=values["addon_names"],
            total_price=values["total_price"],
            date_value=values["date"],
            start_time=values["time"],
            end_time=values["end_time"],
            name=values["name"],
            phone=values["phone"],
            email=values["email"],
            vehicle=values["vehicle"],
            notes=values["notes"],
            location=values["location"] or "Mobile - Customer Location",
            validate_slot=True,
        )

    return {"success": True, "booking": stored_booking}


@app.patch("/api/admin/bookings/{booking_id}", dependencies=[Depends(require_admin)])
async def update_booking(booking_id: str, payload: BookingUpdateRequest) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Booking not found")

        existing = row_to_booking(row)
        addons = payload.addons if payload.addons is not None else existing["addons"]
        addon_names = payload.addon_names if payload.addon_names is not None else existing["addon_names"]
        service = payload.service if payload.service is not None else existing["service"]
        service_name = (
            payload.service_name.strip()
            if payload.service_name is not None and payload.service_name.strip()
            else existing["service_name"]
        )
        service_price = payload.service_price if payload.service_price is not None else existing["service_price"]
        total_price = payload.total_price if payload.total_price is not None else existing["total_price"]
        date_value = payload.date if payload.date is not None else existing["date"]
        start_time = payload.time if payload.time is not None else existing["time"]

        schedule_fields_changed = any(
            value is not None
            for value in (payload.service, payload.addons, payload.date, payload.time, payload.end_time)
        )
        if payload.end_time is not None and payload.end_time:
            end_time = payload.end_time
        elif schedule_fields_changed:
            end_time = minutes_to_time(time_to_minutes(start_time) + service_duration(service, addons))
        else:
            end_time = existing["end_time"]

        name = payload.name if payload.name is not None else existing["name"]
        phone = payload.phone if payload.phone is not None else existing["phone"]
        email = payload.email if payload.email is not None else existing["email"]
        vehicle = payload.vehicle if payload.vehicle is not None else existing["vehicle"]
        notes = payload.notes if payload.notes is not None else existing["notes"]
        location = payload.location if payload.location is not None else existing["location"]

        if not str(name).strip() or not str(phone).strip():
            raise HTTPException(status_code=400, detail="Name and phone are required")
        parse_date(date_value)
        validate_time_range(start_time, end_time)

        if existing["status"] == "confirmed":
            available, reason = slot_availability(
                conn,
                date_value,
                start_time,
                end_time,
                ignore_booking_id=booking_id,
            )
            if not available:
                raise HTTPException(status_code=409, detail=reason)

        conn.execute(
            """
            UPDATE bookings
            SET service = ?, service_name = ?, service_price = ?, addons_json = ?,
                addon_names_json = ?, total_price = ?, date = ?, time = ?,
                end_time = ?, name = ?, phone = ?, email = ?, vehicle = ?,
                notes = ?, location = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                service,
                service_name,
                int(service_price or 0),
                json.dumps(addons),
                json.dumps(addon_names),
                int(total_price or 0),
                date_value,
                start_time,
                end_time,
                str(name).strip(),
                str(phone).strip(),
                str(email or "").strip(),
                str(vehicle or "").strip(),
                str(notes or "").strip(),
                str(location or "").strip(),
                now_iso(),
                booking_id,
            ),
        )
        updated = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()

    return {"success": True, "booking": row_to_booking(updated)}


@app.patch("/api/admin/bookings/{booking_id}/restore", dependencies=[Depends(require_admin)])
async def restore_booking(booking_id: str) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Booking not found")
        booking = row_to_booking(row)
        available, reason = slot_availability(
            conn,
            booking["date"],
            booking["time"],
            booking["end_time"],
            ignore_booking_id=booking_id,
        )
        if not available:
            raise HTTPException(status_code=409, detail=reason)
        conn.execute(
            """
            UPDATE bookings
            SET status = 'confirmed', cancelled_reason = '', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), booking_id),
        )
        updated = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    return {"success": True, "booking": row_to_booking(updated)}


@app.patch("/api/admin/bookings/{booking_id}/complete", dependencies=[Depends(require_admin)])
async def complete_booking(booking_id: str) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Booking not found")
        conn.execute(
            """
            UPDATE bookings
            SET status = 'completed', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), booking_id),
        )
        updated = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    return {"success": True, "booking": row_to_booking(updated)}


@app.post("/api/admin/blockouts", dependencies=[Depends(require_admin)])
async def create_blockout(payload: BlockoutRequest) -> Dict[str, Any]:
    parse_date(payload.date)
    validate_time_range(payload.start_time, payload.end_time)
    blockout_id = str(uuid.uuid4())
    with db() as conn:
        available, reason = slot_availability(conn, payload.date, payload.start_time, payload.end_time)
        if not available:
            raise HTTPException(status_code=409, detail=reason)
        conn.execute(
            """
            INSERT INTO blockouts (id, date, start_time, end_time, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                blockout_id,
                payload.date,
                payload.start_time,
                payload.end_time,
                payload.reason.strip() or "Unavailable",
                now_iso(),
            ),
        )
        row = conn.execute("SELECT * FROM blockouts WHERE id = ?", (blockout_id,)).fetchone()
    return {"success": True, "blockout": row_to_blockout(row)}


@app.delete("/api/admin/blockouts/{blockout_id}", dependencies=[Depends(require_admin)])
async def delete_blockout(blockout_id: str) -> Dict[str, Any]:
    with db() as conn:
        cur = conn.execute("DELETE FROM blockouts WHERE id = ?", (blockout_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Blockout not found")
    return {"success": True}


@app.put("/api/admin/blockouts/{blockout_id}", dependencies=[Depends(require_admin)])
async def update_blockout(blockout_id: str, payload: BlockoutRequest) -> Dict[str, Any]:
    parse_date(payload.date)
    validate_time_range(payload.start_time, payload.end_time)
    with db() as conn:
        row = conn.execute("SELECT * FROM blockouts WHERE id = ?", (blockout_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Blockout not found")
        available, reason = slot_availability(
            conn,
            payload.date,
            payload.start_time,
            payload.end_time,
            ignore_blockout_id=blockout_id,
        )
        if not available:
            raise HTTPException(status_code=409, detail=reason)
        conn.execute(
            """
            UPDATE blockouts
            SET date = ?, start_time = ?, end_time = ?, reason = ?
            WHERE id = ?
            """,
            (
                payload.date,
                payload.start_time,
                payload.end_time,
                payload.reason.strip() or "Unavailable",
                blockout_id,
            ),
        )
        updated = conn.execute("SELECT * FROM blockouts WHERE id = ?", (blockout_id,)).fetchone()
    return {"success": True, "blockout": row_to_blockout(updated)}


@app.put("/api/admin/working-hours", dependencies=[Depends(require_admin)])
async def update_working_hours(payload: WorkingHoursRequest) -> Dict[str, Any]:
    seen = set()
    with db() as conn:
        for item in payload.hours:
            if item.weekday < 0 or item.weekday > 6:
                raise HTTPException(status_code=400, detail="Weekday must be 0-6")
            if item.weekday in seen:
                raise HTTPException(status_code=400, detail="Duplicate weekday in payload")
            seen.add(item.weekday)
            if item.is_open:
                validate_time_range(item.start_time, item.end_time)
            conn.execute(
                """
                INSERT INTO working_hours (weekday, is_open, start_time, end_time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(weekday) DO UPDATE SET
                    is_open = excluded.is_open,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time
                """,
                (item.weekday, int(item.is_open), item.start_time, item.end_time),
            )
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT weekday, is_open, start_time, end_time FROM working_hours ORDER BY weekday"
            ).fetchall()
        ]
    return {"success": True, "working_hours": rows}


@app.patch("/api/admin/subscriptions/{subscription_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_subscription(subscription_id: str, payload: CancelRequest) -> Dict[str, Any]:
    cutoff = today_local().isoformat()
    with db() as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Subscription not found")
        timestamp = now_iso()
        conn.execute(
            "UPDATE subscriptions SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (timestamp, subscription_id),
        )
        conn.execute(
            """
            UPDATE bookings
            SET status = 'cancelled', cancelled_reason = ?, updated_at = ?
            WHERE subscription_id = ? AND date >= ? AND status = 'confirmed'
            """,
            (payload.reason.strip() or "Subscription cancelled", timestamp, subscription_id, cutoff),
        )
        updated = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
    return {"success": True, "subscription": row_to_subscription(updated)}


@app.patch("/api/admin/subscriptions/{subscription_id}", dependencies=[Depends(require_admin)])
async def update_subscription(subscription_id: str, payload: SubscriptionUpdateRequest) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Subscription not found")
        current = dict(row)

        updates = dict(current)
        for field_name in (
            "name",
            "phone",
            "email",
            "vehicle",
            "vehicle_reg",
            "vehicle_colour",
            "condition",
            "preferred_day",
            "preferred_time",
            "start_date",
            "visit_time",
            "visit_end_time",
            "postcode",
            "address",
            "notes",
        ):
            value = getattr(payload, field_name)
            if value is not None:
                updates[field_name] = value.strip()

        if not updates["name"].strip() or not updates["phone"].strip():
            raise HTTPException(status_code=400, detail="Name and phone are required")
        parse_date(updates["start_date"])

        preferred_time_changed = payload.preferred_time is not None and payload.visit_time is None
        if preferred_time_changed:
            updates["visit_time"] = time_window_to_start(updates["preferred_time"])
            updates["visit_end_time"] = minutes_to_time(time_to_minutes(updates["visit_time"]) + 120)
        elif payload.visit_time is not None and payload.visit_end_time is None:
            updates["visit_end_time"] = minutes_to_time(time_to_minutes(updates["visit_time"]) + 120)
        validate_time_range(updates["visit_time"], updates["visit_end_time"])

        timestamp = now_iso()
        conn.execute(
            """
            UPDATE subscriptions
            SET name = ?, phone = ?, email = ?, vehicle = ?, vehicle_reg = ?,
                vehicle_colour = ?, condition = ?, preferred_day = ?,
                preferred_time = ?, start_date = ?, visit_time = ?,
                visit_end_time = ?, postcode = ?, address = ?, notes = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                updates["name"].strip(),
                updates["phone"].strip(),
                updates["email"].strip(),
                updates["vehicle"].strip(),
                updates["vehicle_reg"].strip(),
                updates["vehicle_colour"].strip(),
                updates["condition"].strip(),
                updates["preferred_day"].strip(),
                updates["preferred_time"].strip(),
                updates["start_date"],
                updates["visit_time"],
                updates["visit_end_time"],
                updates["postcode"].strip(),
                updates["address"].strip(),
                updates["notes"].strip(),
                timestamp,
                subscription_id,
            ),
        )
        updated_row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        updated_subscription = dict(updated_row)
        location = subscription_location(updated_subscription)
        cutoff = today_local().isoformat()
        conn.execute(
            """
            UPDATE bookings
            SET name = ?, phone = ?, email = ?, vehicle = ?, location = ?, updated_at = ?
            WHERE subscription_id = ? AND date >= ? AND status = 'confirmed'
            """,
            (
                updated_subscription["name"],
                updated_subscription["phone"],
                updated_subscription["email"],
                updated_subscription["vehicle"],
                location,
                timestamp,
                subscription_id,
                cutoff,
            ),
        )

        generated_visits: List[Dict[str, Any]] = []
        if payload.regenerate_future_visits:
            conn.execute(
                """
                UPDATE bookings
                SET status = 'cancelled', cancelled_reason = ?, updated_at = ?
                WHERE subscription_id = ? AND type = 'subscription_visit'
                    AND date >= ? AND status = 'confirmed'
                """,
                ("Subscription schedule regenerated", timestamp, subscription_id, cutoff),
            )
            first_target = max(today_local(), parse_date(updated_subscription["start_date"]))
            generated_visits = generate_future_subscription_visits(
                conn,
                updated_subscription,
                first_target,
                SUBSCRIPTION_VISITS_TO_GENERATE,
            )

        final_row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()

    return {
        "success": True,
        "subscription": row_to_subscription(final_row),
        "generated_visits": generated_visits,
    }


@app.post("/api/admin/subscriptions/{subscription_id}/visits", dependencies=[Depends(require_admin)])
async def add_subscription_visit(subscription_id: str, payload: SubscriptionVisitRequest) -> Dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Subscription not found")
        subscription = dict(row)
        if subscription["status"] != "active":
            raise HTTPException(status_code=400, detail="Subscription is not active")

        duration = payload.duration_minutes
        validate_exact_slot = bool(payload.date)
        if payload.date:
            date_value = parse_date(payload.date).isoformat()
            start_time = payload.time or subscription["visit_time"]
            end_time = minutes_to_time(time_to_minutes(start_time) + duration)
            validate_time_range(start_time, end_time)
        else:
            latest_row = conn.execute(
                """
                SELECT MAX(date) AS date
                FROM bookings
                WHERE subscription_id = ? AND status = 'confirmed'
                """,
                (subscription_id,),
            ).fetchone()
            latest_date = latest_row["date"] if latest_row else ""
            target = today_local()
            if latest_date:
                target = max(target, add_months(parse_date(latest_date), 1))
            target = adjust_to_preferred_weekday(target, subscription.get("preferred_day") or "")
            date_value, start_time, end_time = find_next_available_slot(
                conn,
                target,
                subscription["visit_time"],
                duration,
                max_days=30,
            )

        booking = create_subscription_visit(
            conn,
            subscription,
            date_value=date_value,
            start_time=start_time,
            end_time=end_time,
            service_name=payload.service_name.strip() or "Monthly Maintenance Valet",
            total_price=payload.total_price,
            validate_slot=validate_exact_slot,
        )

    return {"success": True, "booking": booking}


@app.get("/api/admin/day", dependencies=[Depends(require_admin)])
async def admin_day(
    date: str,
    service: str = "essential-valet",
    addons: str = "",
) -> Dict[str, Any]:
    addon_list = parse_addons_param(addons)
    with db() as conn:
        day_bookings = [
            row_to_booking(row)
            for row in conn.execute(
                """
                SELECT * FROM bookings
                WHERE date = ?
                ORDER BY time
                """,
                (date,),
            ).fetchall()
        ]
        day_blockouts = [
            row_to_blockout(row)
            for row in conn.execute(
                """
                SELECT * FROM blockouts
                WHERE date = ?
                ORDER BY start_time
                """,
                (date,),
            ).fetchall()
        ]
        hours = working_hours_for_date(conn, date)
        slots = available_slots_for_date(conn, date, service, addon_list)
    return {
        "date": parse_date(date).isoformat(),
        "working_hours": dict(hours) if hours else None,
        "bookings": day_bookings,
        "blockouts": day_blockouts,
        "available_slots": slots,
    }


@app.get("/calendar/tks-services.ics", dependencies=[Depends(require_calendar_token)])
async def calendar_feed() -> Response:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start_window = (today_local() - timedelta(days=30)).isoformat()
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TKS Services//Booking Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:TKS Services Bookings",
        f"X-WR-TIMEZONE:{LOCAL_TZ_NAME}",
    ]

    with db() as conn:
        booking_rows = conn.execute(
            """
            SELECT * FROM bookings
            WHERE status = 'confirmed' AND date >= ?
            ORDER BY date, time
            """,
            (start_window,),
        ).fetchall()
        for row in booking_rows:
            booking = row_to_booking(row)
            summary = f"TKS - {booking['service_name']} - {booking['name']}"
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{booking['calendar_uid']}",
                    f"DTSTAMP:{now_stamp}",
                    f"DTSTART:{ics_datetime(booking['date'], booking['time'])}",
                    f"DTEND:{ics_datetime(booking['date'], booking['end_time'])}",
                    f"SUMMARY:{ics_escape(summary)}",
                    f"DESCRIPTION:{ics_escape(booking_description(booking))}",
                    f"LOCATION:{ics_escape(booking.get('location') or 'Mobile - Customer Location')}",
                    "STATUS:CONFIRMED",
                    "END:VEVENT",
                ]
            )

        blockout_rows = conn.execute(
            """
            SELECT * FROM blockouts
            WHERE date >= ?
            ORDER BY date, start_time
            """,
            (start_window,),
        ).fetchall()
        for row in blockout_rows:
            blockout = row_to_blockout(row)
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:blockout-{blockout['id']}@tks-services",
                    f"DTSTAMP:{now_stamp}",
                    f"DTSTART:{ics_datetime(blockout['date'], blockout['start_time'])}",
                    f"DTEND:{ics_datetime(blockout['date'], blockout['end_time'])}",
                    f"SUMMARY:{ics_escape('Blocked - ' + blockout['reason'])}",
                    f"DESCRIPTION:{ics_escape('Unavailable: ' + blockout['reason'])}",
                    "STATUS:CONFIRMED",
                    "TRANSP:OPAQUE",
                    "END:VEVENT",
                ]
            )

    lines.append("END:VCALENDAR")
    return Response(
        "\r\n".join(lines) + "\r\n",
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="tks-services.ics"'},
    )


if __name__ == "__main__":
    import uvicorn

    if ADMIN_TOKEN == "change-me":
        print("WARNING: ADMIN_TOKEN is using the local default. Set ADMIN_TOKEN before deployment.")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
