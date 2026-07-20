# AtharvCast (NapSnap) - Complete Engineering Architecture & System Postmortem

## Executive Summary
This document provides a comprehensive, end-to-end postmortem and architectural record of the entire engineering journey for the **NapSnap Baby Monitor System** (BeagleBone Black Hub + ESP8266 Radar Sensor Node).

It captures all architectural evolutions, software design patterns, hardware fallacies, concurrency bugs, logging strategies, and UI optimizations developed to create a robust, production-grade system.

---

## 1. System Architecture Overview

```
+-----------------------------------------------------------------------------------+
|                            BEAGLEBONE BLACK HUB                                   |
|  Flask Server (server.py)                                                         |
|  - RLock Thread Safety           - Dynamic Dashboard (/)                          |
|  - Async PyChromecast            - Radar Tuning Engine (/calibration)             |
|  - Rotating File Logger (500KB)  - Full System Logs Viewer (/logs)                |
|  - Auto-Sync Hardware State      - Live Spectrum Proxy (/api/radar_telemetry)     |
+----------------------------------------^------------------------------------------+
                                         |
                       HTTP GET / POST over Wi-Fi LAN
                                         |
+----------------------------------------v------------------------------------------+
|                            ESP8266 SENSOR NODE                                    |
|  NodeMCU Firmware (esp8266_radar.ino)                                             |
|  - Sliding Window Filter (Option C)  - SoftwareSerial @ 115,200 baud (D5/D6)       |
|  - Nick Reynolds ld2410 C++ Library  - Real-Time Engineering Telemetry            |
|  - ArduinoOTA (Password Protected)   - 10-min Heartbeat Keepalive                 |
+----------------------------------------^------------------------------------------+
                                         |
                       115200 Baud UART Serial Stream
                                         |
+----------------------------------------v------------------------------------------+
|                            HLK-LD2410C RADAR SENSOR                               |
|  24GHz FMCW Millimeter-Wave Radar                                                 |
|  - 9 Distance Gates (0.75m - 6.75m)  - Independent OUT Pin (GPIO 4 / D2)          |
+-----------------------------------------------------------------------------------+
```

---

## 2. Motion Detection & Wakeup Filtering (Option C)

### The Problem
Raw radar outputs from mmWave sensors are extremely sensitive to transient micro-movements, electrical noise, or passing pets, causing frequent false alarms if triggered on the first positive reading.

### The Solution: Option C (Sliding Window / Duty Cycle Filter)
Evaluated multiple algorithms (Hysteresis, Leaky Bucket, Sliding Window, Two-Point Checkpoints) in `review/sustained_window_proposal.md`. Selected **Option C**:
* **Buffer Structure**: 150-sample circular buffer (`bool samples[150]`), sampling every $100\text{ ms}$ ($15\text{ seconds}$ total evaluation window).
* **Threshold Criteria**: Sustained motion must be present in $\ge 70\%$ of the window ($105/150$ active samples) before confirming a cognitive wakeup alert.
* **Buffer Management**: Automatically clears the sliding window buffer (`memset(samples, 0, sizeof(samples))`) when the system is **Disarmed** or entering the **2-minute Cooldown Period** to prevent stale motion accumulation.

---

## 3. Server Architecture, Concurrency & PyChromecast Audio

### Issue 1: PyChromecast Blocking & Speaker Flooding
* **Problem**: Synchronous PyChromecast calls (`pychromecast.get_listed_chromecasts()`, `play_media()`, `block_until_active()`) blocked Flask's main HTTP thread for 5–15 seconds. Frequent button toggles or alerts caused HTTP route timeouts, frozen UIs, and speaker crashes.
* **Fix**:
  * **Asynchronous Spawning**: Wrapped all audio casting in daemon background threads (`threading.Thread(target=_cast_audio_worker, args=(filename,), daemon=True).start()`).
  * **Direct IP Discovery**: Configured `known_hosts=[MINI_IP]` to bypass slow mDNS broadcasts.
  * **Resource Cleanup**: Wrapped discovery in `try...finally: pychromecast.discovery.stop_discovery(browser)` to guarantee mDNS sockets close even on error.
  * **Dynamic Volume**: Integrated `CURRENT_VOLUME` state so volume changes apply instantly to outgoing audio casts.

### Issue 2: Recursive Lock Deadlocks
* **Problem**: Using a standard non-reentrant `threading.Lock()` caused a deadlock when route handlers (e.g. `send_command()`) acquired `state_lock` and then called `add_log()`, which also attempted to acquire `state_lock`.
* **Fix**: Upgraded `state_lock` to a reentrant lock (`threading.RLock()`), allowing the same thread to acquire the lock recursively without blocking itself.

### Issue 3: Connection Auto-Healing & Heartbeats
* **Problem**: If the ESP8266 rebooted or triggered an alert, the UI displayed `OFFLINE` because `LAST_HEARTBEAT_TIME` was only refreshed during `/register`.
* **Fix**: Updated `/trigger` and `/command/<action>` routes to refresh `LAST_HEARTBEAT_TIME = time.time()` under lock. Added IP auto-healing if the ESP's dynamic IP changes.

---

## 4. Server Logging & IST Log Rotation

### Strategy & Noise Reduction
* **Problem**: ESP8266 10-minute keepalive heartbeats flooded the dashboard's recent log view with repetitive messages.
* **Fix**:
  * **Dual Logging Pipeline**: Unchanged keepalive heartbeats are logged strictly to the rotating log file via `logger.info()`, excluding them from the top-20 UI memory list (`recent_logs`).
  * **Log Rotation**: Implemented `RotatingFileHandler('baby_monitor.log', maxBytes=500000, backupCount=3)` to cap disk usage at ~1.5MB.
  * **IST Timezone Formatting**: Configured `ist_converter()` using `pytz.timezone('Asia/Kolkata')` so log timestamps match local IST time.
  * **Full Log Viewer (`/logs`)**: Added a dedicated route displaying archived logs in reverse chronological order.

---

## 5. Web UI & AJAX Architecture

### Dynamic Status Polling & Button Guardrails
* **Problem**: Legacy 30s full-page reloads caused page flickering and allowed users to click invalid buttons (e.g. clicking ARM when already ARMED).
* **Fix**:
  * **JSON API Endpoint (`/api/status`)**: Polled by JavaScript `fetch()` every 5 seconds.
  * **Dynamic Button Locking**:
    * When `ARMED`: ARM button disabled, DISARM enabled.
    * When `DISARMED`: DISARM button disabled, ARM enabled.
    * When `OFFLINE`: Both buttons disabled, offline note displayed.

---

## 6. Hardware & Serial Communication Fallacies

### Fallacy 1: SoftwareSerial at 256,000 Baud
* **The Assumption**: `SoftwareSerial` on ESP8266 could bit-bang $256,000\text{ baud}$ reliably.
* **The Reality**: Bit time at $256,000\text{ baud}$ is $3.90\,\mu\text{s}$. Wi-Fi interrupts routinely freeze CPU execution for $15 - 20\,\mu\text{s}$, causing 100% packet loss.
* **The Fix**: Reconfigured both the radar hardware and `SoftwareSerial` to **$115,200\text{ baud}$** ($8.68\,\mu\text{s}$ bit window), providing 2.2× wider timing tolerance.

### Fallacy 2: NodeMCU Pin D8 (GPIO 15) Onboard Pull-Down Conflict
* **The Assumption**: Any digital pin pair could be used for SoftwareSerial.
* **The Reality**: On NodeMCU boards, **Pin D8 (GPIO 15)** has a physical **$10\text{ k}\Omega$ pull-down resistor to GND** (required for SPI Flash boot mode). The pull-down attenuated 3.3V logic HIGH pulses from SoftwareSerial.
* **The Fix**: Moved `SoftwareSerial` to pins **D5 (GPIO 14 - RX)** and **D6 (GPIO 12 - TX)**, which have zero pull-down resistors or boot restrictions.

### Fallacy 3: Unloaded Voltage vs. Voltage Drop Under Load
* **The Assumption**: Measuring $5.0\text{V}$ across unplugged jumper wires meant the radar was properly powered.
* **The Reality**: Under load ($\sim 100\text{ mA}$), a damaged Dupont jumper wire dropped $3.0\text{V}$, leaving **only $2.0\text{V}$** across the HLK pins ($V = I \times R$), causing brown-out resets.
* **The Fix**: Diagnosed the loaded voltage drop, replaced the defective jumper wires, and verified $4.9\text{V} - 5.0\text{V}$ across the HLK pins under load.

### Fallacy 4: Blocking `delay(100)` RX Buffer Overflow
* **The Assumption**: A `delay(100)` at the end of `loop()` provided CPU stability.
* **The Reality**: During a $100\text{ ms}$ CPU sleep, over 2,500 bytes arrived, overflowing the 64-byte `SoftwareSerial` RX buffer and corrupting packet headers (`0xF4 0xF3 0xF2 0xF1`).
* **The Fix**: Replaced `delay(100)` with `yield()`, allowing `radar.read()` to drain bytes continuously.

### Fallacy 5: Hardware UART Swap (`Serial.swap()`) Freezing Console Output
* **The Assumption**: `Serial.swap()` would transparently upgrade UART0 to hardware processing.
* **The Reality**: `Serial.swap()` physically rerouted UART0 TX/RX away from the Micro-USB CH340 chip onto D8/D7, freezing the PC USB Serial Monitor at `Registrati`.
* **The Fix**: Restored `Serial.begin(115200)` for Micro-USB console logging and isolated radar UART on `SoftwareSerial(14, 12)`.

### Fallacy 6: Non-Existent API Methods in C++ Libraries
* **The Assumption**: The `ld2410` library required manual `radar.requestStartConfig()` and `radar.requestEndConfig()` calls.
* **The Reality**: Inspecting `ld2410.h` revealed that Nick Reynolds' library encapsulates config mode entry and exit **internally** inside `setMaxValues()` and `setGateSensitivityThreshold()`.
* **The Fix**: Removed invalid method calls, aligning code directly with library headers.

### Fallacy 7: Bluetooth Active Connection Lockout
* **The Assumption**: Bluetooth pairing and physical UART streaming could operate concurrently.
* **The Reality**: The HLK-LD2410 firmware mutex-locks its physical UART port whenever an active Bluetooth connection is established with a smartphone.
* **The Fix**: Disconnecting the HLKRadarTool app immediately released the hardware UART port.

---

## 7. Web Calibration Engine (`/calibration`)

### Features Implemented
1. **Live Engineering Energy Spectrum**: Renders 9 gate rows with live animated green (moving) and blue (static) energy bars and dynamic red threshold lines.
2. **Auto-Sync on Load**: Queries `http://{ESP_IP}/get_config` on page load to populate sliders with actual hardware thresholds.
3. **Offline Guardrails**: Disables all controls (`<fieldset disabled>`) and displays a warning banner when the ESP8266 node is `OFFLINE`.
4. **Navigation Header**: Top navigation bar for switching between `/`, `/calibration`, and `/logs`.

---

## 8. Summary File Map

* **ESP8266 Firmware**: [esp8266_radar.ino](file:///home/anshul/Desktop/AtharvCast/esp8266_radar/esp8266_radar.ino)
* **BeagleBone Hub Server**: [server.py](file:///home/anshul/Desktop/AtharvCast/server/server.py)
* **Hardware Guide**: [hardware.md](file:///home/anshul/Desktop/AtharvCast/hardware.md)
* **Motion Filter Proposal**: [sustained_window_proposal.md](file:///home/anshul/Desktop/AtharvCast/review/sustained_window_proposal.md)
* **Postmortem Document**: [RADAR_CALIBRATION_POSTMORTEM.md](file:///home/anshul/Desktop/AtharvCast/RADAR_CALIBRATION_POSTMORTEM.md)
