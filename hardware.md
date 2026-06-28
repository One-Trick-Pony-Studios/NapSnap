# NapSnap Hardware Guide

This document details the wiring and components for the NapSnap sensor node.

## Component List
| Component | Purpose |
| :--- | :--- |
| **NodeMCU ESP8266** | Main controller and Wi-Fi interface |
| **HLK-LD2410C** | 24GHz mmWave radar for micro-movement detection |
| **MAX4466** | Microphone amplifier for sound detection |
| **SPDT Throw Switch** | Physical Arm/Disarm toggle |
| **Status LED** | Visual indicator for Armed (ON) / Disarmed (OFF) |

## Pinout Mapping

| Component | Pin on Component | ESP8266 Pin |
| :--- | :--- | :--- |
| **HLK-LD2410C** | VCC | VIN (5V) |
| | GND | GND |
| | OUT | D2 |
| **MAX4466** | VCC | 3V3 |
| | GND | GND |
| | OUT | A0 |
| **Throw Switch** | Terminal 1 (Common) | D1 |
| | Terminal 2 | GND |
| **Status LED** | Anode | D5 (via 330Ω resistor) |
| | Cathode | GND |

## Wiring Notes
- **Voltage:** The radar module (HLK-LD2410C) is connected to **VIN (5V)** to ensure the onboard LDO regulator functions correctly.
- **Button Logic:** The SPDT switch connects D1 to GND when "Armed." The internal pull-up resistor on the ESP8266 is used in the code to detect this state.
- **Placement:** Mount the enclosure high on the wall, angled downward (45-90 degrees) to create a detection cone over the sleeping area.
