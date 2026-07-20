# NapSnap Hardware Guide

This document details the wiring and components for the NapSnap sensor node.

## Component List
| Component | Purpose |
| :--- | :--- |
| **NodeMCU ESP8266** | Main controller and Wi-Fi interface |
| **HLK-LD2410C** | 24GHz mmWave radar for micro-movement detection & 8-gate spatial tuning |
| **MAX4466** | Microphone amplifier for sound detection (optional / TODO) |

## Pinout Mapping

| Component | Pin on Component | ESP8266 Pin | Purpose |
| :--- | :--- | :--- | :--- |
| **HLK-LD2410C** | VCC | VIN (5V) | Power Supply |
| | GND | GND | Ground |
| | OUT | D2 (GPIO 4) | Digital Motion Signal (HIGH / LOW) |
| | TX | D5 (GPIO 14) | Radar Serial TX → ESP SoftwareSerial RX |
| | RX | D6 (GPIO 12) | Radar Serial RX ← ESP SoftwareSerial TX |
| **MAX4466** | VCC | 3V3 | Power (TODO) |
| | GND | GND | Ground |
| | OUT | A0 | Analog Sound Signal (TODO) |

## Wiring & Configuration Notes
- **Voltage:** The radar module (HLK-LD2410C) is connected to **VIN (5V)** to ensure the onboard LDO regulator functions correctly.
- **UART Calibration:** The radar's TX and RX pins are connected to ESP8266 pins D5 and D6. This allows tuning distance gates and sensitivities via the web interface (`/calibration`).
- **Placement:** Mount the enclosure high on the wall, angled downward (45-90 degrees) to create a detection cone over the sleeping area.
