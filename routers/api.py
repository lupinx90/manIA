import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import recurring_ical_events
from fastapi import APIRouter, HTTPException, Query, Response
from icalendar import Calendar

from db import get_db_connection
from schemas import EventCreate, NapCreate, NetworkStatusReport

logger = logging.getLogger(__name__)
router = APIRouter()
MIN_DESIRED_SLEEP_HOURS = 7.5
SLEEP_SUMMARY_DAYS = 6
CALENDAR_FEED_TIMEOUT_SECONDS = 10


@router.get("/api/network-status")
def get_network_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT online, targets_json, updated_at FROM network_status WHERE id = 1"
        ).fetchone()
    if row is None:
        return {
            "status": "UNKNOWN",
            "online": None,
            "targets": [],
            "updated_at": None,
        }
    return {
        "status": "ON" if row["online"] else "OFF",
        "online": bool(row["online"]),
        "targets": json.loads(row["targets_json"]),
        "updated_at": row["updated_at"],
    }


@router.post("/api/network-status")
def report_network_status(report: NetworkStatusReport):
    targets = [target.model_dump(mode="json") for target in report.targets]
    online = any(target["online"] for target in targets)
    updated_at = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO network_status (id, online, targets_json, updated_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET online = excluded.online, "
            "targets_json = excluded.targets_json, updated_at = excluded.updated_at",
            (int(online), json.dumps(targets), updated_at),
        )
    return {
        "status": "ON" if online else "OFF",
        "online": online,
        "targets": targets,
        "updated_at": updated_at,
    }


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
            recorded = duration_hours >= 4
            duration_hours = round(duration_hours, 2)
            deficit_hours = round(max(0.0, MIN_DESIRED_SLEEP_HOURS - duration_hours), 2)
            if recorded:
                conn.execute(
                    "INSERT INTO sleep_logs (sleep_start, wake_time, duration_hours, deficit_hours, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (row["sleep_start_time"], wake_str, duration_hours, deficit_hours, wake_str),
                )
            log_info = {
                "duration_hours": duration_hours,
                "deficit_hours": deficit_hours,
                "recorded": recorded,
            }

        conn.execute(
            "UPDATE sleep_state SET state = 'AWAKE', sleep_start_time = NULL, last_updated = ? WHERE id = 1",
            (wake_str,),
        )
    return {"status": "success", "state": "AWAKE", "wake_time": wake_str, "log": log_info}


@router.get("/api/sleep-logs")
def get_sleep_logs(limit: int | None = Query(default=None, ge=1)):
    query = (
        "SELECT id, sleep_start, wake_time, duration_hours, deficit_hours, timestamp "
        "FROM sleep_logs ORDER BY timestamp DESC, id DESC"
    )
    params = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with get_db_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]


@router.post("/api/naps", status_code=201)
def create_nap(nap: NapCreate):
    nap_date = date.today().isoformat()
    timestamp = datetime.now().isoformat()
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO nap_logs (nap_date, duration_hours, timestamp) VALUES (?, ?, ?)",
            (nap_date, nap.hours, timestamp),
        )
        row = conn.execute(
            "SELECT SUM(duration_hours) AS nap_hours FROM nap_logs WHERE nap_date = ?",
            (nap_date,),
        ).fetchone()

    return {
        "status": "success",
        "added_hours": nap.hours,
        "nap_hours": round(float(row["nap_hours"] or 0), 2),
        "nap_date": nap_date,
    }


@router.put("/api/naps")
def set_daily_nap_total(nap: NapCreate):
    nap_date = date.today().isoformat()
    timestamp = datetime.now().isoformat()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM nap_logs WHERE nap_date = ?", (nap_date,))
        conn.execute(
            "INSERT INTO nap_logs (nap_date, duration_hours, timestamp) VALUES (?, ?, ?)",
            (nap_date, nap.hours, timestamp),
        )

    return {
        "status": "success",
        "nap_hours": nap.hours,
        "nap_date": nap_date,
    }


@router.get("/api/sleep-summary")
def get_sleep_summary():
    today = datetime.now().date()
    first_day = today - timedelta(days=SLEEP_SUMMARY_DAYS - 1)
    days_by_date = {}
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT wake_time, duration_hours FROM sleep_logs "
            "WHERE substr(wake_time, 1, 10) >= ? AND substr(wake_time, 1, 10) <= ? "
            "ORDER BY wake_time",
            (first_day.isoformat(), today.isoformat()),
        ).fetchall()
        nap_rows = conn.execute(
            "SELECT nap_date, SUM(duration_hours) AS nap_hours FROM nap_logs "
            "WHERE nap_date >= ? AND nap_date <= ? GROUP BY nap_date",
            (first_day.isoformat(), today.isoformat()),
        ).fetchall()

    for row in rows:
        wake_time = row["wake_time"]
        duration_hours = row["duration_hours"]
        if not wake_time or duration_hours is None:
            continue

        try:
            wake_date = date.fromisoformat(wake_time[:10])
        except ValueError:
            continue

        day = days_by_date.setdefault(wake_date, {"sleep_hours": 0.0})
        day["sleep_hours"] = round(day["sleep_hours"] + float(duration_hours), 2)

    for row in nap_rows:
        try:
            nap_date = date.fromisoformat(row["nap_date"])
        except ValueError:
            continue

        day = days_by_date.setdefault(
            nap_date,
            {"sleep_hours": 0.0, "nap_hours": 0.0},
        )
        day["nap_hours"] = round(float(row["nap_hours"] or 0), 2)

    days = []
    for offset in range(SLEEP_SUMMARY_DAYS):
        current_date = first_day + timedelta(days=offset)
        day = days_by_date.get(current_date, {})
        original_sleep_hours = round(day.get("sleep_hours", 0.0), 2)
        nap_hours = round(day.get("nap_hours", 0.0), 2)
        sleep_hours = (
            round(original_sleep_hours + nap_hours, 2)
            if current_date in days_by_date
            else None
        )
        goal_met = sleep_hours is not None and sleep_hours >= MIN_DESIRED_SLEEP_HOURS
        deficit_hours = (
            round(max(0.0, MIN_DESIRED_SLEEP_HOURS - sleep_hours), 2)
            if sleep_hours is not None
            else None
        )
        days.append({
            "date": current_date.isoformat(),
            "original_sleep_hours": original_sleep_hours,
            "nap_hours": nap_hours,
            "sleep_hours": sleep_hours,
            "goal_met": goal_met,
            "deficit_hours": deficit_hours,
        })

    tracked_days = [day for day in days if day["sleep_hours"] is not None]
    return {
        "goal_hours": MIN_DESIRED_SLEEP_HOURS,
        "days": days,
        "average_sleep_hours": (
            round(sum(day["sleep_hours"] for day in tracked_days) / len(tracked_days), 2)
            if tracked_days else None
        ),
        "goal_met_days": sum(1 for day in tracked_days if day["goal_met"]),
        "tracked_days": len(tracked_days),
        "total_deficit_hours": round(
            sum(day["deficit_hours"] for day in tracked_days if day["deficit_hours"] is not None),
            2,
        ),
    }


@router.get("/api/events")
def get_events(
    response: Response,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    show_secret: bool = Query(default=False),
):
    response.headers["X-Google-Calendar-Configured"] = str(
        bool(os.getenv("GOOGLE_CALENDAR_ICAL_URL"))
    ).lower()
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
