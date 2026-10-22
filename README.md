# Sleep & Schedule Hub

A local FastAPI dashboard for logging bedtime/wake-up times and 1-, 2-, or 3-hour naps, tracking combined daily sleep against a 7.5-hour goal, and viewing local events alongside a Google Calendar iCal feed.

## Run locally

### Raspberry Pi OS

These steps work on current Raspberry Pi OS releases. A 64-bit installation is recommended. Copy or clone the project onto the Pi, open a terminal in the project folder, then install Python and create a virtual environment:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Start the dashboard:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

On the Pi, open <http://127.0.0.1:8000> in a browser. Stop the server with `Ctrl+C`; leave the virtual environment with `deactivate`. The SQLite database is saved as `system_hub.db` in the project folder.

To open the dashboard from another device on your trusted local network, bind it to all interfaces instead:

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://<raspberry-pi-ip>:8000` on that device (find the Pi's address with `hostname -I`). This app has no user authentication, so only enable network access on a trusted private network and do not expose it to the internet. For optional Google Calendar setup, see [Connect Google Calendar](#connect-google-calendar).

### Ubuntu

Install Python and the virtual-environment package, then create and activate a virtual environment:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`; leave the virtual environment with `deactivate`.

To show Google Calendar events, set the iCal URL in the same terminal before starting the server:

```bash
export GOOGLE_CALENDAR_ICAL_URL="https://calendar.google.com/calendar/ical/..."
python -m uvicorn main:app --reload
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>.

## Run with Docker

Install Docker Engine and the Docker Compose plugin, then from the project folder run:

```bash
docker compose up --build -d
docker compose ps
```

Open <http://127.0.0.1:8000>. The service binds to localhost, runs as a non-root user, and stores its SQLite database in a named volume so data survives container recreation. To stop it without removing data:

```bash
docker compose down
```

To remove the app and its saved database:

```bash
docker compose down --volumes
```

## ESP32 target IP status

The dashboard's **Network Status** panel reports **ON** when at least one configured target IP responds to ping and **OFF** when none respond. Before receiving a report it shows **UNKNOWN**. The ESP32 checks each target periodically and posts individual results to `/api/network-status`; manIA stores the latest report in its database.

The ESP32 must reach manIA over your trusted local network. Docker Compose binds the web app to the manIA host's private LAN IP (`192.168.18.131` by default). If your host uses a different IP, set `MANIA_BIND_ADDRESS` to its private LAN IP in `.env`; this setting overrides the Compose default, so update any existing `127.0.0.1` value. Recreate the service with `docker compose up --build -d`. Configure the same manIA URL, target IPv4 addresses, and Wi-Fi credentials in the PlatformIO project at [`esp32_status_monitor/`](./esp32_status_monitor/). Do not bind this unauthenticated app to a public interface or expose it to the internet.

## Connect Google Calendar

1. In Google Calendar, open **Settings** for the calendar you want to display.
2. Under **Integrate calendar**, copy **Secret address in iCal format**.
3. Copy `.env.example` to `.env` and paste the address after `GOOGLE_CALENDAR_ICAL_URL=`. The app reads this file when started directly with Python, and Docker Compose also loads it automatically. Keep `.env` private and do not commit it.

For a one-off direct Python run, you can set the value in the shell instead. In Ubuntu or another Bash shell:

   ```bash
   export GOOGLE_CALENDAR_ICAL_URL="https://calendar.google.com/calendar/ical/..."
   python -m uvicorn main:app --reload
   ```

   In PowerShell, use `$env:GOOGLE_CALENDAR_ICAL_URL = "https://calendar.google.com/calendar/ical/..."` instead.

The URL is only read by the server and is never sent to the browser. For Docker Compose, start the service after saving the value to `.env`:

```bash
docker compose up --build -d
```

Recreate the container after changing the URL so it receives the updated value. Google Calendar events are read-only; locally created events remain editable. Recurring events are expanded for the date range requested by FullCalendar. Google events marked `PRIVATE` or `CONFIDENTIAL` display as “Private event” until the **Reveal Secret Appointments** control is enabled.

The Google iCal URL grants access to calendar data. This app does not provide user authentication, so keep it bound to localhost or expose it only on a trusted private network. Do not publish the app or its event API to the internet without adding authentication.

## Verify

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```
