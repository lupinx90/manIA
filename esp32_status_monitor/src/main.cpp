#include <Arduino.h>
#include <ESP32Ping.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include <cstring>

#include "secrets.h"

#ifndef STATUS_API_URL
#error "Set STATUS_API_URL in platformio.ini"
#endif

#ifndef TARGET_IPS
#error "Set TARGET_IPS in platformio.ini"
#endif

#ifndef CHECK_INTERVAL_MS
#define CHECK_INTERVAL_MS 300000
#endif

namespace {

constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 20000;
unsigned long lastCheck = 0;
bool firstCheck = true;

bool connectToWiFi() {
    if (WiFi.status() == WL_CONNECTED) {
        return true;
    }

    Serial.printf("Connecting to Wi-Fi: %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    const unsigned long startedAt = millis();
    while (WiFi.status() != WL_CONNECTED &&
           millis() - startedAt < WIFI_CONNECT_TIMEOUT_MS) {
        delay(250);
        Serial.print('.');
    }
    Serial.println();

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("Wi-Fi connection failed; will retry on the next check.");
        return false;
    }

    Serial.print("Connected, IP address: ");
    Serial.println(WiFi.localIP());
    return true;
}

bool checkTargetsAndReport() {
    char configuredTargets[sizeof(TARGET_IPS)];
    std::strcpy(configuredTargets, TARGET_IPS);

    String payload = "{\"targets\":[";
    unsigned int checkedTargets = 0;
    bool firstTarget = true;
    char* token = std::strtok(configuredTargets, ",");

    while (token != nullptr) {
        String addressText(token);
        addressText.trim();
        IPAddress address;
        if (!address.fromString(addressText)) {
            Serial.printf("Invalid target IPv4 address: %s\n", addressText.c_str());
            token = std::strtok(nullptr, ",");
            continue;
        }

        const bool online = Ping.ping(address, 1);
        Serial.printf("%s %s\n", addressText.c_str(), online ? "ON" : "OFF");
        if (!firstTarget) {
            payload += ',';
        }
        payload += "{\"ip\":\"";
        payload += addressText;
        payload += "\",\"online\":";
        payload += online ? "true" : "false";
        payload += '}';
        firstTarget = false;
        ++checkedTargets;
        token = std::strtok(nullptr, ",");
    }

    if (checkedTargets == 0) {
        Serial.println("No valid target IPs configured; status report skipped.");
        return false;
    }

    payload += "]}";
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(5000);
    if (!http.begin(client, STATUS_API_URL)) {
        Serial.println("Could not initialize the manIA status request.");
        return false;
    }

    http.addHeader("Content-Type", "application/json");
    const int responseCode = http.POST(payload);
    if (responseCode >= 200 && responseCode < 300) {
        Serial.printf("manIA status report accepted (HTTP %d).\n", responseCode);
    } else if (responseCode < 0) {
        Serial.printf("manIA status request failed: %s\n",
                      http.errorToString(responseCode).c_str());
    } else {
        Serial.printf("manIA rejected the status report (HTTP %d).\n", responseCode);
    }
    http.end();
    return responseCode >= 200 && responseCode < 300;
}

}  // namespace

void setup() {
    Serial.begin(115200);
    WiFi.setSleep(false);
    connectToWiFi();
}

void loop() {
    const unsigned long now = millis();
    if (firstCheck || now - lastCheck >= CHECK_INTERVAL_MS) {
        firstCheck = false;
        lastCheck = now;
        if (connectToWiFi()) {
            checkTargetsAndReport();
        }
    }
    delay(250);
}
