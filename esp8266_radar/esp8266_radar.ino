#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ESP8266WebServer.h>
#include <WiFiClient.h>
#include <ESP8266mDNS.h>
#include <WiFiUdp.h>
#include <ArduinoOTA.h>

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

  delay(100); // 100ms stability delay
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