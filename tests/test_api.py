from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from main import app
from routers import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    monkeypatch.delenv("GOOGLE_CALENDAR_ICAL_URL", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def test_sleep_buttons_record_session_and_deficit(client):
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Sleep & Schedule Hub" in dashboard.text
    assert "Target IP availability" in dashboard.text
    assert 'id="network-status-badge"' in dashboard.text
    assert 'id="network-target-list"' in dashboard.text
    assert "fetch('/api/network-status'" in dashboard.text
    assert "Last 6 Days" in dashboard.text
    assert 'aria-label="Nighty Night"' in dashboard.text
    assert 'aria-label="Good Morning"' in dashboard.text
    assert "Log bedtime start timestamp" not in dashboard.text
    assert "Stop the sleep timer" not in dashboard.text
    assert client.get("/api/state").json()["state"] == "AWAKE"

    night = client.post("/api/action/night").json()
    assert night["state"] == "ASLEEP"

    with db.get_db_connection() as conn:
        start = (datetime.now() - timedelta(hours=5)).isoformat()
        conn.execute(
            "UPDATE sleep_state SET sleep_start_time = ? WHERE id = 1",
            (start,),
        )

    morning = client.post("/api/action/morning").json()
    assert morning["state"] == "AWAKE"
    assert morning["log"]["duration_hours"] == pytest.approx(5, abs=0.02)
    assert morning["log"]["deficit_hours"] == pytest.approx(2.5, abs=0.02)
    assert len(client.get("/api/sleep-logs").json()) == 1


def test_sleep_summary_covers_last_six_days_and_aggregates_daily_logs(client):
    today = datetime.now().date()
    first_day = today - timedelta(days=5)
    with db.get_db_connection() as conn:
        for wake_date, duration in (
            (today - timedelta(days=1), 4.0),
            (today - timedelta(days=1), 2.0),
            (today - timedelta(days=2), 8.0),
            (today - timedelta(days=5), 7.5),
            (today - timedelta(days=6), 9.0),
            (today + timedelta(days=1), 9.0),
        ):
            wake_time = datetime.combine(wake_date, datetime.min.time()).isoformat()
            conn.execute(
                "INSERT INTO sleep_logs "
                "(sleep_start, wake_time, duration_hours, deficit_hours, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (wake_time, wake_time, duration, 0, wake_time),
            )

    response = client.get("/api/sleep-summary")
    assert response.status_code == 200
    summary = response.json()
    assert len(summary["days"]) == 6
    assert summary["days"][0]["date"] == first_day.isoformat()
    assert summary["days"][-1]["date"] == today.isoformat()
    assert summary["days"][-2]["sleep_hours"] == 6.0
    assert summary["days"][-2]["deficit_hours"] == 1.5
    assert summary["days"][-3]["sleep_hours"] == 8.0
    assert summary["days"][-3]["goal_met"] is True
    assert summary["days"][0]["sleep_hours"] == 7.5
    assert summary["days"][0]["goal_met"] is True
    assert summary["days"][-4]["sleep_hours"] is None
    assert summary["tracked_days"] == 3
    assert summary["goal_met_days"] == 2
    assert summary["average_sleep_hours"] == 7.17
    assert summary["total_deficit_hours"] == 1.5


def test_sleep_summary_handles_no_recorded_sleep(client):
    summary = client.get("/api/sleep-summary").json()

    assert summary["average_sleep_hours"] is None
    assert summary["goal_met_days"] == 0
    assert summary["tracked_days"] == 0
    assert summary["total_deficit_hours"] == 0
    assert all(day["sleep_hours"] is None for day in summary["days"])


def test_repeated_night_does_not_reset_sleep_start(client):
    first = client.post("/api/action/night").json()["sleep_start"]
    second = client.post("/api/action/night").json()["sleep_start"]
    assert second == first


def test_morning_while_awake_does_not_create_sleep_log(client):
    response = client.post("/api/action/morning")
    assert response.json()["log"] is None
    assert client.get("/api/sleep-logs").json() == []


def test_morning_does_not_record_sleep_shorter_than_four_hours(client):
    client.post("/api/action/night")

    with db.get_db_connection() as conn:
        start = (datetime.now() - timedelta(hours=3.5)).isoformat()
        conn.execute(
            "UPDATE sleep_state SET sleep_start_time = ? WHERE id = 1",
            (start,),
        )

    response = client.post("/api/action/morning")

    assert response.json()["log"]["duration_hours"] < 4
    assert response.json()["log"]["recorded"] is False
    assert client.get("/api/sleep-logs").json() == []


def test_network_status_is_on_when_any_target_is_online(client):
    unknown = client.get("/api/network-status")
    assert unknown.status_code == 200
    assert unknown.headers["cache-control"] == "no-store"
    assert unknown.json() == {
        "status": "UNKNOWN",
        "online": None,
        "targets": [],
        "updated_at": None,
    }

    report = client.post(
        "/api/network-status",
        json={
            "targets": [
                {"ip": "192.168.1.10", "online": False},
                {"ip": "192.168.1.11", "online": True},
            ],
        },
    )
    assert report.status_code == 200
    assert report.json()["status"] == "ON"
    assert report.json()["online"] is True

    status_response = client.get("/api/network-status")
    assert status_response.headers["cache-control"] == "no-store"
    status = status_response.json()
    assert status["status"] == "ON"
    assert status["targets"] == [
        {"ip": "192.168.1.10", "online": False},
        {"ip": "192.168.1.11", "online": True},
    ]
    assert status["updated_at"] is not None


def test_network_status_is_off_when_no_targets_are_online(client):
    response = client.post(
        "/api/network-status",
        json={"targets": [{"ip": "192.168.1.10", "online": False}]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "OFF"
    assert response.json()["online"] is False


def test_network_status_rejects_invalid_ip_addresses(client):
    response = client.post(
        "/api/network-status",
        json={"targets": [{"ip": "router.local", "online": True}]},
    )

    assert response.status_code == 422


def test_local_calendar_events_and_validation(client):
    created = client.post(
        "/api/events",
        json={
            "title": "Visible event",
            "start_time": "2026-10-11T09:00:00",
            "category": "personal",
        },
    )
    assert created.status_code == 201
    client.post(
        "/api/events",
        json={
            "title": "Hidden event",
            "start_time": "2026-10-11T10:00:00",
            "is_secret": True,
        },
    )

    default_events = client.get("/api/events").json()
    assert [event["title"] for event in default_events] == ["Visible event"]
    assert client.get("/api/events").headers["x-google-calendar-configured"] == "false"
    revealed_events = client.get("/api/events?show_secret=true").json()
    assert {event["title"] for event in revealed_events} == {
        "Visible event",
        "🔒 Hidden event",
    }

    invalid = client.post(
        "/api/events",
        json={
            "title": "Invalid range",
            "start_time": "2026-10-11T10:00:00",
            "end_time": "2026-10-11T09:00:00",
        },
    )
    assert invalid.status_code == 422


def test_google_calendar_feed_expands_recurring_events_and_masks_private_details(
    client, monkeypatch
):
    feed = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Tests//Calendar Feed//EN
BEGIN:VEVENT
UID:private-event@example.test
DTSTAMP:20261001T120000Z
DTSTART:20261012T090000Z
DTEND:20261012T100000Z
RRULE:FREQ=DAILY;COUNT=2
CLASS:PRIVATE
SUMMARY:Personal appointment
DESCRIPTION:Sensitive notes
END:VEVENT
BEGIN:VEVENT
UID:all-day-event@example.test
DTSTAMP:20261001T120000Z
DTSTART;VALUE=DATE:20261013
DTEND;VALUE=DATE:20261014
SUMMARY:All-day event
END:VEVENT
END:VCALENDAR
"""

    class FeedResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.close()

    monkeypatch.setenv(
        "GOOGLE_CALENDAR_ICAL_URL",
        "https://calendar.google.com/calendar/ical/private/basic.ics",
    )
    start = "2026-10-11T00:00:00+02:00"
    end = "2026-10-15T00:00:00+02:00"
    with patch.object(
        api, "urlopen", side_effect=lambda *args, **kwargs: FeedResponse(feed)
    ) as fetch_feed:
        hidden = client.get(
            "/api/events",
            params={"start": start, "end": end},
        )
        assert hidden.status_code == 200
        assert hidden.headers["x-google-calendar-configured"] == "true"
        hidden_events = hidden.json()
        assert len(hidden_events) == 3
        private_events = [
            event for event in hidden_events if event["extendedProps"]["is_secret"]
        ]
        assert len(private_events) == 2
        assert all(event["title"] == "🔒 Private event" for event in private_events)
        assert all(event["extendedProps"]["description"] == "" for event in private_events)
        assert next(
            event for event in hidden_events if event["title"] == "All-day event"
        )["allDay"]
        assert fetch_feed.call_args.args[0].startswith(
            "https://calendar.google.com/"
        )

        revealed = client.get(
            "/api/events",
            params={"start": start, "end": end, "show_secret": "true"},
        ).json()
        assert len(revealed) == 3
        assert sum(event["title"] == "Personal appointment" for event in revealed) == 2


def test_google_calendar_feed_rejects_non_google_urls(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ICAL_URL", "https://example.com/calendar.ics")
    response = client.get("/api/events")
    assert response.status_code == 500
    assert "calendar.google.com" in response.json()["detail"]
