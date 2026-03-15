#!/usr/bin/env python3
"""TKS Services Booking API — connects to Google Calendar for availability and booking."""

import asyncio
import json
import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Service durations in minutes
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
    "monthly-subscription": 120,
}

# Business hours: 8am - 6pm, Monday - Saturday
BUSINESS_START = 8
BUSINESS_END = 18
SLOT_INTERVAL = 30  # minutes between slots


async def call_tool(source_id, tool_name, arguments):
    proc = await asyncio.create_subprocess_exec(
        "external-tool", "call", json.dumps({
            "source_id": source_id, "tool_name": tool_name, "arguments": arguments,
        }),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        error_text = stderr.decode()
        print(f"Tool error: {error_text}")
        raise RuntimeError(error_text)
    return json.loads(stdout.decode())


def get_available_slots(date_str: str, booked_events: list, service_duration: int) -> list:
    """Generate available time slots for a given date, excluding booked times."""
    date = datetime.strptime(date_str, "%Y-%m-%d")
    
    # Skip Sundays
    if date.weekday() == 6:
        return []
    
    slots = []
    current = date.replace(hour=BUSINESS_START, minute=0, second=0, microsecond=0)
    end_of_day = date.replace(hour=BUSINESS_END, minute=0, second=0, microsecond=0)
    
    while current + timedelta(minutes=service_duration) <= end_of_day:
        slot_end = current + timedelta(minutes=service_duration)
        
        # Check if this slot conflicts with any booked event
        is_available = True
        for event in booked_events:
            event_start = event.get("start", "")
            event_end = event.get("end", "")
            if not event_start or not event_end:
                continue
            
            try:
                # Parse ISO format dates
                ev_start = datetime.fromisoformat(event_start.replace("Z", "+00:00")).replace(tzinfo=None)
                ev_end = datetime.fromisoformat(event_end.replace("Z", "+00:00")).replace(tzinfo=None)
                
                # Check overlap
                if current < ev_end and slot_end > ev_start:
                    is_available = False
                    break
            except (ValueError, TypeError):
                continue
        
        if is_available:
            slots.append({
                "time": current.strftime("%H:%M"),
                "display": current.strftime("%-I:%M %p"),
                "end_time": slot_end.strftime("%H:%M"),
                "end_display": slot_end.strftime("%-I:%M %p"),
            })
        
        current += timedelta(minutes=SLOT_INTERVAL)
    
    return slots


@app.get("/api/available-dates")
async def get_available_dates(service: str = "essential-valet"):
    """Get available dates for the next 14 days."""
    duration = SERVICE_DURATIONS.get(service, 60)
    dates = []
    today = datetime.now()
    
    for i in range(1, 15):
        date = today + timedelta(days=i)
        # Skip Sundays
        if date.weekday() == 6:
            continue
        dates.append({
            "date": date.strftime("%Y-%m-%d"),
            "day_name": date.strftime("%A"),
            "display": date.strftime("%d %B"),
            "short": date.strftime("%d %b"),
        })
    
    return {"dates": dates, "service": service, "duration": duration}


@app.get("/api/available-slots")
async def get_available_slots_endpoint(date: str, service: str = "essential-valet"):
    """Get available time slots for a specific date."""
    duration = SERVICE_DURATIONS.get(service, 60)
    
    try:
        # Search calendar for events on this date
        result = await call_tool("gcal", "search_calendar", {
            "start_date": f"{date}T00:00:00+00:00",
            "end_date": f"{date}T23:59:59+00:00",
            "queries": [""],
        })
        
        # Parse the events from the result
        booked_events = []
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if isinstance(content, str):
                try:
                    events_data = json.loads(content)
                    if isinstance(events_data, list):
                        for ev in events_data:
                            booked_events.append({
                                "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "")),
                                "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "")),
                            })
                except json.JSONDecodeError:
                    pass
            elif isinstance(content, list):
                for ev in content:
                    booked_events.append({
                        "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "")),
                        "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "")),
                    })
        elif isinstance(result, list):
            for ev in result:
                if isinstance(ev, dict):
                    booked_events.append({
                        "start": ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "")),
                        "end": ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "")),
                    })
        
        slots = get_available_slots(date, booked_events, duration)
        
    except Exception as e:
        print(f"Calendar error: {e}")
        # Fallback: return all slots if calendar unavailable
        slots = get_available_slots(date, [], duration)
    
    return {"date": date, "service": service, "duration": duration, "slots": slots}


class BookingRequest(BaseModel):
    service: str
    service_name: str
    service_price: int = 0
    addons: list = []
    addon_names: list = []
    total_price: int = 0
    date: str
    time: str
    end_time: str
    name: str
    phone: str
    email: str = ""
    vehicle: str = ""
    notes: str = ""


@app.post("/api/book")
async def create_booking(booking: BookingRequest):
    """Create a booking and add to Google Calendar."""
    duration = SERVICE_DURATIONS.get(booking.service, 60)
    
    start_dt = f"{booking.date}T{booking.time}:00+00:00"
    end_dt = f"{booking.date}T{booking.end_time}:00+00:00"
    
    addons_text = ""
    if booking.addon_names:
        addons_text = "\nAdd-ons: " + ", ".join(booking.addon_names)
    
    description = f"""TKS Services Booking
Service: {booking.service_name} (\u00a3{booking.service_price}){addons_text}
Total: \u00a3{booking.total_price}
Customer: {booking.name}
Phone: {booking.phone}
Email: {booking.email}
Vehicle: {booking.vehicle}
Notes: {booking.notes}
---
Booked via TKS Services website"""
    
    try:
        result = await call_tool("gcal", "update_calendar", {
            "create_actions": [{
                "action": "create",
                "title": f"TKS Valet - {booking.service_name} - {booking.name}",
                "description": description,
                "start_date_time": start_dt,
                "end_date_time": end_dt,
                "location": "Mobile - Customer Location",
                "attendees": [],
                "meeting_provider": None,
            }],
            "delete_actions": [],
            "update_actions": [],
            "user_prompt": None,
        })
        
        return {
            "success": True,
            "message": f"Booking confirmed for {booking.service_name} on {booking.date} at {booking.time}",
            "booking": {
                "service": booking.service_name,
                "date": booking.date,
                "time": booking.time,
                "name": booking.name,
            }
        }
    except Exception as e:
        print(f"Booking error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create booking. Please try again or call us directly.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
