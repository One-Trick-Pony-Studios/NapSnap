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
const int LED_PIN = 14;       
bool isArmed = false;

// --- TIMERS ---
unsigned long lastTriggerTime = 0; 
const unsigned long COOLDOWN_PERIOD = 120000; 
unsigned long lastHeartbeat = 0; 
const unsigned long HEARTBEAT_INTERVAL = 600000; // 10 minutes in milliseconds

// --- WEB SERVER ---
ESP8266WebServer server(80);

void setup() {
  Serial.begin(115200);
  pinMode(RADAR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

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
    digitalWrite(LED_PIN, HIGH);
    server.send(200, "text/plain", "Radar Armed");
    Serial.println("Received Command: ARMED");
  });

  server.on("/disarm", []() {
    isArmed = false;
    digitalWrite(LED_PIN, LOW);
    server.send(200, "text/plain", "Radar Disarmed");
    Serial.println("Received Command: DISARMED");
  });

  server.begin();
  Serial.println("HTTP Server Started.");

  // Initialize OTA
  ArduinoOTA.setHostname("BabyMonitorNode"); // Name it so it appears in IDE
  ArduinoOTA.begin();
}

void loop() {
  ArduinoOTA.handle(); // Crucial: This listens for new code packets
  server.handleClient();

  // --- THE HEARTBEAT ---
  if (millis() - lastHeartbeat >= HEARTBEAT_INTERVAL) {
    registerWithHub();
    lastHeartbeat = millis();
  }

  if (isArmed) {
    unsigned long currentMillis = millis();
    if (currentMillis - lastTriggerTime >= COOLDOWN_PERIOD || lastTriggerTime == 0) {
      if (digitalRead(RADAR_PIN) == HIGH) {
        Serial.println("\n--- WAKEUP DETECTED ---");
        sendAlert();
        lastTriggerTime = currentMillis; 
      }
    }
  }
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