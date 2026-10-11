import logging
import os
from datetime import date, datetime, timedelta
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import recurring_ical_events
from fastapi import APIRouter, HTTPException, Query
from icalendar import Calendar

from db import get_db_connection
from schemas import EventCreate

logger = logging.getLogger(__name__)
router = APIRouter()
MIN_DESIRED_SLEEP_HOURS = 7.5
CALENDAR_FEED_TIMEOUT_SECONDS = 10


def _fetch_google_calendar_events(start: datetime, end: datetime, show_secret: bool):
    feed_url = os.getenv("GOOGLE_CALENDAR_ICAL_URL")
    if not feed_url:
        return []

    if feed_url.startswith("webcal://"):
        feed_url = "https://" + feed_url[len("webcal://"):]
    parsed_url = urlsplit(feed_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "calendar.google.com":
        logger.error("GOOGLE_CALENDAR_ICAL_URL must be an HTTPS calendar.google.com URL")
        raise HTTPException(
            status_code=500,
            detail="Google Calendar feed configuration must use an HTTPS calendar.google.com URL.",
        )

    try:
        with urlopen(feed_url, timeout=CALENDAR_FEED_TIMEOUT_SECONDS) as response:
            calendar_data = response.read(5_000_001)
        if len(calendar_data) > 5_000_000:
            raise HTTPException(status_code=502, detail="Google Calendar feed is larger than 5 MB.")

        calendar = Calendar.from_ical(calendar_data)
        components = recurring_ical_events.of(calendar).between(start, end)
    except HTTPException:
        raise
    except (URLError, TimeoutError, OSError, TypeError, ValueError) as exc:
        logger.exception("Unable to fetch or parse the Google Calendar feed")
        raise HTTPException(
            status_code=502,
            detail="Unable to load the Google Calendar feed. Check the configured feed URL and try again.",
        ) from exc

    events = []
    for component in components:
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue

        start_property = component.get("DTSTART")
        if start_property is None:
            continue
        event_start = start_property.dt
        end_property = component.get("DTEND")
        event_end = end_property.dt if end_property else None
        is_secret = str(component.get("CLASS", "")).upper() in {"PRIVATE", "CONFIDENTIAL"}
        title = str(component.get("SUMMARY", "Untitled event"))
        description = str(component.get("DESCRIPTION", ""))
        location = str(component.get("LOCATION", ""))
        if is_secret and not show_secret:
            title = "🔒 Private event"
            description = ""
            location = ""

        uid = str(component.get("UID", f"{event_start.isoformat()}:{title}"))
        events.append({
            "id": f"google:{uid}:{event_start.isoformat()}",
            "title": title,
            "start": event_start.isoformat(),
            "end": event_end.isoformat() if event_end else None,
            "allDay": isinstance(event_start, date) and not isinstance(event_start, datetime),
            "backgroundColor": "#0ea5e9",
            "borderColor": "#0284c7",
            "extendedProps": {
                "category": "google",
                "description": description,
                "location": location,
                "is_secret": is_secret,
                "source": "google",
            },
        })
    return events


@router.get("/api/state")
def get_state():
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT state, sleep_start_time, last_updated FROM sleep_state WHERE id = 1"
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Sleep state has not been initialized.")
    return {
        "state": row["state"],
        "sleep_start_time": row["sleep_start_time"],
        "last_updated": row["last_updated"],
    }


@router.post("/api/action/night")
def action_good_night():
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT state, sleep_start_time FROM sleep_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="Sleep state has not been initialized.")
        start_time = row["sleep_start_time"] if row["state"] == "ASLEEP" else now
        conn.execute(
            "UPDATE sleep_state SET state = 'ASLEEP', sleep_start_time = ?, last_updated = ? WHERE id = 1",
            (start_time, now),
        )
    return {"status": "success", "state": "ASLEEP", "sleep_start": start_time}


@router.post("/api/action/morning")
def action_good_morning():
    wake_time = datetime.now()
    wake_str = wake_time.isoformat()
    log_info = None
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT sleep_start_time FROM sleep_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="Sleep state has not been initialized.")

        if row["sleep_start_time"]:
            sleep_start = datetime.fromisoformat(row["sleep_start_time"])
            duration_hours = max(0, (wake_time - sleep_start).total_seconds() / 3600)
            duration_hours = round(duration_hours, 2)
            deficit_hours = round(max(0.0, MIN_DESIRED_SLEEP_HOURS - duration_hours), 2)
            conn.execute(
                "INSERT INTO sleep_logs (sleep_start, wake_time, duration_hours, deficit_hours, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (row["sleep_start_time"], wake_str, duration_hours, deficit_hours, wake_str),
            )
            log_info = {
                "duration_hours": duration_hours,
                "deficit_hours": deficit_hours,
                "recorded": True,
            }

        conn.execute(
            "UPDATE sleep_state SET state = 'AWAKE', sleep_start_time = NULL, last_updated = ? WHERE id = 1",
            (wake_str,),
        )
    return {"status": "success", "state": "AWAKE", "wake_time": wake_str, "log": log_info}


@router.get("/api/sleep-logs")
def get_sleep_logs():
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, sleep_start, wake_time, duration_hours, deficit_hours, timestamp "
            "FROM sleep_logs ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return [dict(row) for row in rows]


@router.get("/api/events")
def get_events(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    show_secret: bool = Query(default=False),
):
    range_start = start or (datetime.now() - timedelta(days=31))
    range_end = end or (datetime.now() + timedelta(days=366))
    if (range_start.utcoffset() is None) != (range_end.utcoffset() is None):
        raise HTTPException(
            status_code=422,
            detail="Calendar range boundaries must use matching time zones.",
        )
    if range_end <= range_start:
        raise HTTPException(status_code=422, detail="Calendar range end must be after its start.")

    with get_db_connection() as conn:
        if show_secret:
            rows = conn.execute(
                "SELECT id, title, start_time, end_time, category, description, is_secret FROM calendar_events"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, start_time, end_time, category, description, is_secret "
                "FROM calendar_events WHERE is_secret = 0"
            ).fetchall()

    color_map = {
        "work": "#6366f1",
        "personal": "#10b981",
        "sleep": "#8b5cf6",
        "secret": "#ec4899",
    }
    events = []
    for row in rows:
        category = "secret" if row["is_secret"] else row["category"]
        events.append({
            "id": str(row["id"]),
            "title": f"🔒 {row['title']}" if row["is_secret"] else row["title"],
            "start": row["start_time"],
            "end": row["end_time"],
            "backgroundColor": color_map.get(category, "#3b82f6"),
            "borderColor": color_map.get(category, "#3b82f6"),
            "extendedProps": {
                "category": row["category"],
                "description": row["description"],
                "is_secret": bool(row["is_secret"]),
                "source": "local",
            },
        })

    events.extend(_fetch_google_calendar_events(range_start, range_end, show_secret))
    return events


@router.post("/api/events", status_code=201)
def create_event(event: EventCreate):
    with get_db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO calendar_events (title, start_time, end_time, category, description, is_secret) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.title,
                event.start_time.isoformat(),
                event.end_time.isoformat() if event.end_time else None,
                event.category,
                event.description,
                int(event.is_secret),
            ),
        )
        event_id = cursor.lastrowid
    return {"status": "success", "id": event_id}


@router.delete("/api/events/{event_id}")
def delete_event(event_id: int):
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Event not found.")
    return {"status": "success"}
