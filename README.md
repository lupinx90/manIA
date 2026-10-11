# Sleep & Schedule Hub

A local FastAPI dashboard for logging bedtime/wake-up times, tracking sleep deficit against a 7.5-hour goal, and viewing local events alongside a Google Calendar iCal feed.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>.

## Run with Docker

Install Docker Desktop, then from the project folder run:

```powershell
docker compose up --build -d
docker compose ps
```

Open <http://127.0.0.1:8000>. The service binds to localhost, runs as a non-root user, and stores its SQLite database in a named volume so data survives container recreation. To stop it without removing data:

```powershell
docker compose down
```

To remove the app and its saved database:

```powershell
docker compose down --volumes
```

## Connect Google Calendar

1. In Google Calendar, open **Settings** for the calendar you want to display.
2. Under **Integrate calendar**, copy **Secret address in iCal format**.
3. Set it in the process environment as `GOOGLE_CALENDAR_ICAL_URL` before starting the app. For the current PowerShell session:

   ```powershell
   $env:GOOGLE_CALENDAR_ICAL_URL = "https://calendar.google.com/calendar/ical/..."
   python -m uvicorn main:app --reload
   ```

The URL is only read by the server and is never sent to the browser. For Docker Compose, set the same variable in PowerShell before starting the service:

```powershell
$env:GOOGLE_CALENDAR_ICAL_URL = "https://calendar.google.com/calendar/ical/..."
docker compose up --build -d
```

Or copy `.env.example` to `.env` and put the URL there; Docker Compose loads that file automatically. Do not commit `.env`. For running the app directly with Python, set the process environment variable as shown above because the app does not load `.env` files itself. Recreate the container after changing the URL so it receives the updated value. Google Calendar events are read-only; locally created events remain editable. Recurring events are expanded for the date range requested by FullCalendar. Google events marked `PRIVATE` or `CONFIDENTIAL` display as “Private event” until the **Reveal Secret Appointments** control is enabled.

The Google iCal URL grants access to calendar data. This app does not provide user authentication, so keep it bound to localhost or expose it only on a trusted private network. Do not publish the app or its event API to the internet without adding authentication.

## Verify

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```
