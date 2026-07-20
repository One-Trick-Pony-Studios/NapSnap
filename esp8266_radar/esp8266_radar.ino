#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ESP8266WebServer.h>
#include <WiFiClient.h>
#include <ESP8266mDNS.h>
#include <WiFiUdp.h>
#include <ArduinoOTA.h>
#include <SoftwareSerial.h>
#include <ld2410.h> // Library by Nick Reynolds

// --- RADAR UART DEFINITIONS ---
// Uses D5 (GPIO 14 - RX) and D6 (GPIO 12 - TX) to avoid D8 10k pull-down resistor signal attenuation
SoftwareSerial radarSerial(14, 12); 
ld2410 radar;

// --- NETWORK CONFIGURATION ---
const char* ssid = "SARA";
const char* password = "csgwireless";
String hubBaseURL = "http://192.168.0.203:5000"; // Your BeagleBone IP

// --- PIN ASSIGNMENTS & STATE ---
const int RADAR_PIN = 4;      
bool isArmed = false;

// --- TIMERS ---
unsigned long lastTriggerTime = 0; 
const unsigned long COOLDOWN_PERIOD = 120000; 
unsigned long lastHeartbeat = 0; 
const unsigned long HEARTBEAT_INTERVAL = 600000; // 10 minutes in milliseconds

// --- SLIDING WINDOW CONFIGURATION ---
const int WINDOW_SIZE = 150;      // 150 samples (15 seconds at 100ms sample rate)
const float TRIGGER_RATIO = 0.70; // Trigger alert if motion is present >70% of the window

// --- SLIDING WINDOW STATE ---
bool samples[WINDOW_SIZE] = {false};
int writeIndex = 0;
int activeCount = 0;
unsigned long lastSampleTime = 0;


// --- WEB SERVER ---
ESP8266WebServer server(80);

void setup() {
  Serial.begin(115200);
  pinMode(RADAR_PIN, INPUT);

  Serial.print("\nConnecting to WiFi");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.print("\nWiFi Connected! My Dynamic IP is: ");
  Serial.println(WiFi.localIP()); 

  // --- THE AUTOMATIC HANDSHAKE ---
  registerWithHub();

  // Initialize SoftwareSerial for LD2410 Radar at 115200 baud
  radarSerial.begin(115200);
  if (radar.begin(radarSerial, false)) { // Non-blocking initialization
    Serial.println("HLK-LD2410 UART SoftwareSerial attached at 115200 baud!");
    radar.requestStartEngineeringMode(); // Enable real-time per-gate energy streaming
  } else {
    Serial.println("HLK-LD2410 UART initialization failed.");
  }

  // --- DEFINE WEB SERVER ROUTES ---
  server.on("/arm", []() {
    isArmed = true;
    lastTriggerTime = 0; 
    server.send(200, "text/plain", "Radar Armed");
    Serial.println("Received Command: ARMED");
  });

  server.on("/disarm", []() {
    isArmed = false;
    server.send(200, "text/plain", "Radar Disarmed");
    Serial.println("Received Command: DISARMED");
  });

  server.on("/config_full", handleFullConfig);
  server.on("/config_gates", handleMultiGateConfig);
  server.on("/get_config", handleGetConfig);
  server.on("/telemetry", handleTelemetry);

  server.begin();
  Serial.println("HTTP Server Started.");

  // Initialize OTA
  ArduinoOTA.setHostname("BabyMonitorNode"); // Name it so it appears in IDE
  ArduinoOTA.setPassword("anshul"); // Secure OTA updates with a password
  ArduinoOTA.begin();
}
void loop() {
  // Handle OTA updates and Web Client routing seamlessly in the background
  ArduinoOTA.handle();
  server.handleClient();
  
  // Continuously process incoming UART frames from the radar
  radar.read();

  unsigned long currentMillis = millis();
  // --- THE HEARTBEAT ---
  if (currentMillis - lastHeartbeat >= HEARTBEAT_INTERVAL) {
    registerWithHub();
    lastHeartbeat = millis();
  }

  if (isArmed) {
    // Check if we are currently outside the 2-minute cooldown window
    if (currentMillis - lastTriggerTime >= COOLDOWN_PERIOD || lastTriggerTime == 0) {
      
      // Sample every 100ms
      if (currentMillis - lastSampleTime >= 100) {
        lastSampleTime = currentMillis;

        bool currentRadarState = (digitalRead(RADAR_PIN) == HIGH);

        // Subtract the oldest sample from activeCount
        activeCount -= samples[writeIndex] ? 1 : 0;

        // Save the new sample and add it to activeCount
        samples[writeIndex] = currentRadarState;
        activeCount += currentRadarState ? 1 : 0;

        // Increment write index (circular buffer)
        writeIndex = (writeIndex + 1) % WINDOW_SIZE;

        // Visual indicator on Serial
        if (currentRadarState) {
          Serial.print("*");
        } else {
          Serial.print(".");
        }

        float ratio = (float)activeCount / WINDOW_SIZE;
        if (ratio >= TRIGGER_RATIO) {
          Serial.println("\n--- COGNITIVE WAKEUP CONFIRMED ---");
          Serial.print("Sustained movement pattern met threshold: ");
          Serial.print(ratio * 100.0);
          Serial.println("% active.");
          
          sendAlert(); // Fire HTTP GET request to the BeagleBone Hub
          
          // Enter background cooldown and clear sliding window buffer
          lastTriggerTime = currentMillis;
          memset(samples, 0, sizeof(samples));
          activeCount = 0;
          writeIndex = 0;
        }
      }
    } else {
      // Cooldown state indicator: print once per second to avoid flooding
      static unsigned long lastCooldownPrint = 0;
      if (currentMillis - lastCooldownPrint >= 1000) {
        Serial.print("-");
        lastCooldownPrint = currentMillis;
      }
      
      // Keep buffer cleared during cooldown
      memset(samples, 0, sizeof(samples));
      activeCount = 0;
      writeIndex = 0;
    }
  } else {
    // System is disarmed, completely clear sliding window
    memset(samples, 0, sizeof(samples));
    activeCount = 0;
    writeIndex = 0;
  }

  yield(); // Non-blocking CPU yield allowing WiFi and SoftwareSerial background processing
}


// --- HELPER FUNCTIONS ---
void registerWithHub() {
  if (WiFi.status() == WL_CONNECTED) {
    WiFiClient client;
    HTTPClient http;
    
    // Send both IP and current state to the Hub
    String registerURL = hubBaseURL + "/register?ip=" + WiFi.localIP().toString() + 
                         "&state=" + (isArmed ? "arm" : "disarm");
    
    Serial.println("Registering with Hub and syncing state...");
    http.begin(client, registerURL);
    int httpCode = http.GET();
    
    if (httpCode == 200) {
      Serial.println("Registration & Sync successful!");
    }
    http.end();
  }
}

void sendAlert() {
  if (WiFi.status() == WL_CONNECTED) {
    WiFiClient client;
    HTTPClient http;
    String triggerURL = hubBaseURL + "/trigger";
    http.begin(client, triggerURL);
    http.GET();
    http.end();
  }
}

// --- RADAR CONFIGURATION HANDLERS ---
void handleFullConfig() {
  if (server.hasArg("max_g") && server.hasArg("timeout") && server.hasArg("moving") && server.hasArg("static")) {
    uint8_t max_g = server.arg("max_g").toInt();
    uint16_t timeout = server.arg("timeout").toInt();
    String movingParams = server.arg("moving");
    String staticParams = server.arg("static");

    Serial.println("\n[UART] Overwriting Full Radar Configuration...");

    // 1. Set Max Distance Gate and Unmanned Timeout
    bool configSuccess = radar.setMaxValues(max_g, max_g, timeout);

    // 2. Set Sensitivity for each gate (0 to 8)
    int m_lastIdx = 0, s_lastIdx = 0;

    for (uint8_t i = 0; i <= 8; i++) {
      int m_nextIdx = movingParams.indexOf(',', m_lastIdx);
      int s_nextIdx = staticParams.indexOf(',', s_lastIdx);

      uint8_t m_val = (m_nextIdx == -1) ? movingParams.substring(m_lastIdx).toInt() : movingParams.substring(m_lastIdx, m_nextIdx).toInt();
      uint8_t s_val = (s_nextIdx == -1) ? staticParams.substring(s_lastIdx).toInt() : staticParams.substring(s_lastIdx, s_nextIdx).toInt();

      radar.setGateSensitivityThreshold(i, m_val, s_val);

      m_lastIdx = (m_nextIdx == -1) ? -1 : m_nextIdx + 1;
      s_lastIdx = (s_nextIdx == -1) ? -1 : s_nextIdx + 1;

      if (m_lastIdx == 0 || m_lastIdx == -1) m_lastIdx = movingParams.length();
      if (s_lastIdx == 0 || s_lastIdx == -1) s_lastIdx = staticParams.length();
    }

    if (configSuccess) {
      Serial.println("[UART] Full parameters committed to radar flash memory.");
      server.send(200, "text/plain", "OK");
    } else {
      Serial.println("[UART] Radar config update finished.");
      server.send(200, "text/plain", "OK");
    }
  } else {
    server.send(400, "text/plain", "Missing Parameters");
  }
}

void handleMultiGateConfig() {
  if (server.hasArg("moving") && server.hasArg("static")) {
    String movingParams = server.arg("moving");
    String staticParams = server.arg("static");

    Serial.println("\n[UART] Overwriting Multi-Gate Sensitivity Config...");

    int m_lastIdx = 0, s_lastIdx = 0;

    for (uint8_t i = 0; i <= 8; i++) {
      int m_nextIdx = movingParams.indexOf(',', m_lastIdx);
      int s_nextIdx = staticParams.indexOf(',', s_lastIdx);

      uint8_t m_val = (m_nextIdx == -1) ? movingParams.substring(m_lastIdx).toInt() : movingParams.substring(m_lastIdx, m_nextIdx).toInt();
      uint8_t s_val = (s_nextIdx == -1) ? staticParams.substring(s_lastIdx).toInt() : staticParams.substring(s_lastIdx, s_nextIdx).toInt();

      radar.setGateSensitivityThreshold(i, m_val, s_val);

      m_lastIdx = (m_nextIdx == -1) ? -1 : m_nextIdx + 1;
      s_lastIdx = (s_nextIdx == -1) ? -1 : s_nextIdx + 1;

      if (m_lastIdx == 0 || m_lastIdx == -1) m_lastIdx = movingParams.length();
      if (s_lastIdx == 0 || s_lastIdx == -1) s_lastIdx = staticParams.length();
    }

    Serial.println("[UART] Sensitivity parameters committed to radar flash memory.");
    server.send(200, "text/plain", "OK");
  } else {
    server.send(400, "text/plain", "Missing Parameters");
  }
}

void handleGetConfig() {
  if (radar.requestCurrentConfiguration()) {
    String json = "{";
    json += "\"max_gate\":" + String(radar.max_moving_gate) + ",";
    json += "\"timeout\":" + String(radar.sensor_idle_time) + ",";
    json += "\"moving_gates\":[";
    for (int i = 0; i <= 8; i++) {
      json += String(radar.motion_sensitivity[i]);
      if (i < 8) json += ",";
    }
    json += "],\"static_gates\":[";
    for (int i = 0; i <= 8; i++) {
      json += String(radar.stationary_sensitivity[i]);
      if (i < 8) json += ",";
    }
    json += "]}";
    server.send(200, "application/json", json);
  } else {
    server.send(500, "text/plain", "Failed to query radar config");
  }
}

void handleTelemetry() {
  bool outState = (digitalRead(RADAR_PIN) == HIGH);
  
  // Auto-enable engineering mode if not yet retrieved
  if (!radar.engineeringRetrieved()) {
    radar.requestStartEngineeringMode();
  }

  static unsigned long lastPrint = 0;
  if (millis() - lastPrint > 1000) {
    lastPrint = millis();
    Serial.print("[Tele] Pres:");
    Serial.print(radar.presenceDetected() ? "Y" : "N");
    Serial.print(" Mov:");
    Serial.print(radar.movingTargetDetected() ? "Y" : "N");
    Serial.print(" Dist:");
    Serial.print(radar.detectionDistance());
    Serial.print("cm OUT:");
    Serial.print(outState ? "HIGH" : "LOW");
    Serial.print(" EngRetrieved:");
    Serial.print(radar.engineeringRetrieved() ? "Y" : "N");
    Serial.print(" RX_Avail:");
    Serial.println(radarSerial.available());
  }

  String json = "{";
  json += "\"presence\":" + String((radar.presenceDetected() || outState) ? "true" : "false") + ",";
  json += "\"moving_detected\":" + String(radar.movingTargetDetected() ? "true" : "false") + ",";
  json += "\"static_detected\":" + String(radar.stationaryTargetDetected() ? "true" : "false") + ",";
  json += "\"distance\":" + String(radar.detectionDistance()) + ",";
  json += "\"out_pin\":" + String(outState ? "true" : "false") + ",";
  json += "\"moving_energy\":[";
  for (int i = 0; i <= 8; i++) {
    json += String(radar.movingEnergyAtGate(i));
    if (i < 8) json += ",";
  }
  json += "],\"static_energy\":[";
  for (int i = 0; i <= 8; i++) {
    json += String(radar.stationaryEnergyAtGate(i));
    if (i < 8) json += ",";
  }
  json += "]}";
  server.send(200, "application/json", json);
}