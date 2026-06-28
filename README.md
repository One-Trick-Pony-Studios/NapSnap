# NapSnap: Snap the sensors to action if baby's Nap is interrupted.

NapSnap is a local-first IoT baby monitoring system designed to detect movement and sound without relying on cloud-based surveillance. It leverages mmWave radar for micro-movement detection (like breathing) and sound monitoring, keeping your baby's privacy and your home's connectivity secure.

## Architecture
- **Sensor Node (ESP8266):** Monitors the environment using an HLK-LD2410C mmWave radar and (optional) MAX4466 microphone.
- **Hub (BeagleBone Black):** A headless server running a Flask backend, managing system state, logs, and Chromecast audio alerts.
- **Alert System:** Triggers a local `.mp3` chime on a Google Home Mini when movement or sound is detected.

## Server Installation
The server runs on the BeagleBone Black (Debian IoT).

1. **Setup Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Systemd Service:**
The server is managed via `systemd` to ensure 24/7 uptime and auto-restart on boot.
* Copy `babymonitor.service` to `/etc/systemd/system/`.
* Run: `sudo systemctl enable babymonitor.service && sudo systemctl start babymonitor.service`


## ESP Usage

The ESP8266 node is programmed via the Arduino IDE.

1. **Calibration:** Use the HLKRadarTool app via Bluetooth to calibrate sensitivity gates.
2. **State Control:** The system is armed/disarmed via a physical SPDT throw switch connected to D1.
3. **OTA Updates:** The firmware supports ArduinoOTA for remote code updates.

## Hardware Details

See [hardware.md](hardware.md) for pinouts, wiring diagrams, and component lists.
