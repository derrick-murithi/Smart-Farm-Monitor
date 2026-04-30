# 🌱 Smart Farm Monitor

Raspberry Pi + Arduino IoT system that monitors soil moisture, temperature, and humidity. Sends SMS alerts via Africa's Talking API when thresholds are crossed, logs all readings to SQLite, and serves a live web dashboard.

## Hardware

| Component | Pin |
|---|---|
| DHT22 (temp/humidity) | Digital 7 |
| Soil moisture sensor | Analog A0 |
| LDR (light) | Analog A1 |
| Relay (irrigation pump) | Digital 8 |
| Status LED | Digital 13 |

## Quick Start

### 1. Flash Arduino
Open `arduino/farm_sensors.ino` in the Arduino IDE.  
Install libraries: **DHT sensor library** + **ArduinoJson** (Library Manager).  
Flash to your board, then open Serial Monitor at 9600 baud to verify JSON output.

### 2. Run Raspberry Pi Gateway

```bash
pip install -r requirements.txt

# Live mode (with Arduino connected via USB)
python monitor.py

# Demo mode (no hardware required — synthetic data)
python monitor.py --demo
```

Open **http://localhost:5000** for the live dashboard.

### 3. Configure SMS Alerts

Edit `monitor.py`:
```python
ALERT_PHONE = "+254712345678"   # your number
AT_USERNAME = "your_at_username"
AT_API_KEY  = "your_api_key"
```

Sign up for free at [africastalking.com](https://africastalking.com) — sandbox tier available.

## Alert Thresholds

| Metric | Alert condition |
|---|---|
| Soil | raw ADC ≤ 400 (dry) → pump activates |
| Temperature | ≥ 35 °C |
| Humidity | ≤ 30% |

Edit constants at the top of `monitor.py` to adjust.

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Live web dashboard |
| `GET /api/latest` | Latest reading as JSON |
| `GET /api/history` | Last 120 readings as JSON |

## Architecture

```
[Arduino Uno]
  └─ Sensors → JSON over Serial (9600 baud)
       └─ [Raspberry Pi: monitor.py]
              ├─ Writes to farm_data.db (SQLite)
              ├─ Checks alert thresholds
              ├─ Sends SMS via Africa's Talking
              └─ Serves Flask dashboard on :5000
```

## Author

Your Name — CS Major · IoT Portfolio
