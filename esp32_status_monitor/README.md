# manIA ESP32 target status monitor

This PlatformIO project is for an ESP32-C3 using the Arduino framework. It checks configured IPv4 targets with ICMP ping, then reports each target's result to manIA. The web status is **ON** if any target responds, **OFF** if none respond, and **UNKNOWN** until the ESP32's first report.

## Configure

1. Install PlatformIO IDE or the PlatformIO Core CLI.
2. Copy `include/secrets.example.h` to `include/secrets.h` and set your Wi-Fi name and password. The real file is ignored by Git.
3. Edit `platformio.ini`:
   - Set `STATUS_API_URL` to the manIA host's private LAN IP, for example `http://192.168.1.20:8000/api/network-status`.
   - Set `TARGET_IPS` to a comma-separated list of target IPv4 addresses.
   - Adjust `CHECK_INTERVAL_MS` if needed; the default is five minutes.
4. Configure manIA to accept LAN connections as described in the root project README. Keep the service on a trusted private network; the reporting endpoint does not authenticate devices.

## Build, upload, and monitor

From this directory:

```sh
pio run
pio run --target upload
pio device monitor
```

The ESP32 checks targets once after startup and then at the configured interval. Serial output shows each IP's ON/OFF result and whether manIA accepted the report. Target IPs must be IPv4 addresses; invalid entries are logged and skipped.
