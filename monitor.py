"""
╔══════════════════════════════════════════════════════════════════╗
║  Smart Farm Monitor — Raspberry Pi Gateway                      ║
║  Reads JSON from Arduino over Serial                            ║
║  Logs to SQLite · Sends SMS via Africa's Talking API            ║
║  Serves a live web dashboard on port 5000                       ║
║                                                                  ║
║  Author : Your Name                                              ║
║  Stack  : Python · Flask · pyserial · africastalking · SQLite   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
from flask import Flask, jsonify, render_template_string

# ─────────────────────────────────────────────────────────────────
#  CONFIGURATION  (edit these before running)
# ─────────────────────────────────────────────────────────────────
SERIAL_PORT     = "/dev/ttyUSB0"   # or "COM3" on Windows
SERIAL_BAUD     = 9600
DB_PATH         = Path("farm_data.db")
ALERT_PHONE     = "+254700000000"  # SMS recipient

# Africa's Talking credentials (https://africastalking.com)
AT_USERNAME     = "sandbox"        # change to your username in production
AT_API_KEY      = "YOUR_AFRICASTALKING_API_KEY"
AT_SENDER_ID    = None             # None = shortcode, or "YOUR_SENDER_ID"

# Alert cooldown: don't re-send SMS for the same alert within N seconds
ALERT_COOLDOWN  = 300  # 5 minutes

# ─────────────────────────────────────────────────────────────────
#  ANSI
# ─────────────────────────────────────────────────────────────────
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"; X = "\033[0m"

# ─────────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────────
def db_init():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            node        TEXT,
            temperature REAL,
            humidity    REAL,
            soil_raw    INTEGER,
            soil_status TEXT,
            light_pct   INTEGER,
            pump        INTEGER,
            ok          INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT,
            node    TEXT,
            type    TEXT,
            message TEXT,
            sms_sent INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def db_insert_reading(row: dict):
    s = row.get("sensors", {})
    st = row.get("status", {})
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO readings
          (ts, node, temperature, humidity, soil_raw, soil_status, light_pct, pump, ok)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(),
        row.get("node"),
        s.get("temperature_c"),
        s.get("humidity_pct"),
        s.get("soil_raw"),
        s.get("soil_status"),
        s.get("light_pct"),
        int(st.get("pump", False)),
        int(st.get("ok", True)),
    ))
    conn.commit()
    conn.close()


def db_recent(limit=60):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_latest():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM readings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


# ─────────────────────────────────────────────────────────────────
#  SMS ALERTS  (Africa's Talking)
# ─────────────────────────────────────────────────────────────────
_last_alert: dict = {}   # { alert_type: last_sent_timestamp }


def send_sms(message: str, alert_type: str):
    now = time.time()
    if now - _last_alert.get(alert_type, 0) < ALERT_COOLDOWN:
        return  # cooldown active

    _last_alert[alert_type] = now

    try:
        import africastalking
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        sms = africastalking.SMS
        response = sms.send(message, [ALERT_PHONE], sender_id=AT_SENDER_ID)
        print(f"{G}[SMS] Sent alert: {message[:60]}...{X}")

        # Log to DB
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO alerts (ts, type, message, sms_sent) VALUES (?,?,?,1)",
            (datetime.now().isoformat(), alert_type, message)
        )
        conn.commit()
        conn.close()

    except ImportError:
        print(f"{Y}[SMS] africastalking not installed. Message: {message}{X}")
    except Exception as e:
        print(f"{R}[SMS] Failed: {e}{X}")


# ─────────────────────────────────────────────────────────────────
#  ALERT CHECK
# ─────────────────────────────────────────────────────────────────
def check_alerts(row: dict):
    st = row.get("status", {})
    s  = row.get("sensors", {})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    node = row.get("node", "farm-node")

    if st.get("soil_alert"):
        send_sms(
            f"[SmartFarm] {ts} | {node} | ⚠ SOIL DRY "
            f"(raw={s.get('soil_raw')}) — Pump activated.",
            "soil"
        )
    if st.get("temp_alert"):
        send_sms(
            f"[SmartFarm] {ts} | {node} | 🌡 HIGH TEMP "
            f"({s.get('temperature_c')}°C) — Check crops.",
            "temp"
        )


# ─────────────────────────────────────────────────────────────────
#  SERIAL READER THREAD
# ─────────────────────────────────────────────────────────────────
latest_reading: dict = {}
reading_lock = threading.Lock()


def serial_reader():
    global latest_reading
    print(f"{C}[Serial] Connecting to {SERIAL_PORT} @ {SERIAL_BAUD} baud...{X}")

    while True:
        try:
            with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=10) as ser:
                print(f"{G}[Serial] Connected.{X}")
                while True:
                    raw = ser.readline().decode("utf-8", errors="ignore").strip()
                    if not raw or not raw.startswith("{"):
                        continue
                    try:
                        row = json.loads(raw)
                        with reading_lock:
                            latest_reading = row
                        db_insert_reading(row)
                        check_alerts(row)
                        ts = datetime.now().strftime("%H:%M:%S")
                        s  = row.get("sensors", {})
                        st = row.get("status", {})
                        status_icon = "✓" if st.get("ok") else "⚠"
                        print(
                            f"  [{ts}] {status_icon}  "
                            f"T={s.get('temperature_c','?')}°C  "
                            f"H={s.get('humidity_pct','?')}%  "
                            f"Soil={s.get('soil_status','?')}  "
                            f"Light={s.get('light_pct','?')}%  "
                            f"Pump={'ON' if st.get('pump') else 'off'}"
                        )
                    except json.JSONDecodeError:
                        pass  # skip malformed lines

        except serial.SerialException as e:
            print(f"{R}[Serial] {e} — retrying in 5s...{X}")
            time.sleep(5)


# ─────────────────────────────────────────────────────────────────
#  FLASK WEB DASHBOARD
# ─────────────────────────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <meta http-equiv="refresh" content="6"/>
  <title>Smart Farm Monitor</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#060a0d;color:#c8d8e8;font-family:'Space Mono',monospace;font-size:13px;padding:24px}
    h1{color:#00ffb4;font-size:20px;margin-bottom:4px}
    .sub{color:#3d5a72;font-size:11px;margin-bottom:28px}
    .kpis{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:28px}
    .kpi{background:#0d1117;border:1px solid #1a2a38;border-radius:12px;padding:20px 24px;flex:1;min-width:130px}
    .kpi-label{font-size:9px;letter-spacing:2px;color:#3d5a72;margin-bottom:6px}
    .kpi-val{font-size:28px;font-weight:800;color:#00ffb4}
    .kpi-unit{font-size:11px;color:#3d5a72}
    .kpi.alert .kpi-val{color:#ff2d78}
    table{width:100%;border-collapse:collapse;background:#0d1117;border-radius:12px;overflow:hidden}
    th{background:#0a1018;color:#3d5a72;font-size:9px;letter-spacing:2px;padding:10px 16px;text-align:left}
    td{padding:9px 16px;border-bottom:1px solid #0d1a26;font-size:11px}
    tr:last-child td{border:none}
    .ok{color:#00ffb4} .warn{color:#ff2d78} .dim{color:#3d5a72}
  </style>
</head>
<body>
  <h1>🌱 Smart Farm Monitor</h1>
  <div class="sub">Live sensor data · auto-refresh every 6s</div>

  {% if latest %}
  <div class="kpis">
    <div class="kpi {% if latest.temperature and latest.temperature >= 35 %}alert{% endif %}">
      <div class="kpi-label">TEMPERATURE</div>
      <div class="kpi-val">{{ latest.temperature or '—' }}</div>
      <div class="kpi-unit">°C</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">HUMIDITY</div>
      <div class="kpi-val">{{ latest.humidity or '—' }}</div>
      <div class="kpi-unit">%</div>
    </div>
    <div class="kpi {% if latest.soil_status == 'DRY' %}alert{% endif %}">
      <div class="kpi-label">SOIL</div>
      <div class="kpi-val" style="font-size:18px;padding-top:6px">{{ latest.soil_status or '—' }}</div>
      <div class="kpi-unit">raw {{ latest.soil_raw }}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">LIGHT</div>
      <div class="kpi-val">{{ latest.light_pct or '—' }}</div>
      <div class="kpi-unit">%</div>
    </div>
    <div class="kpi {% if latest.pump %}alert{% endif %}">
      <div class="kpi-label">PUMP</div>
      <div class="kpi-val" style="font-size:18px;padding-top:6px">{{ 'ON 💧' if latest.pump else 'OFF' }}</div>
    </div>
  </div>
  {% endif %}

  <table>
    <thead>
      <tr><th>TIME</th><th>TEMP °C</th><th>HUM %</th><th>SOIL</th><th>LIGHT %</th><th>PUMP</th><th>STATUS</th></tr>
    </thead>
    <tbody>
    {% for r in rows %}
      <tr>
        <td class="dim">{{ r.ts[11:19] }}</td>
        <td class="{% if r.temperature and r.temperature >= 35 %}warn{% else %}ok{% endif %}">{{ r.temperature or '—' }}</td>
        <td>{{ r.humidity or '—' }}</td>
        <td class="{% if r.soil_status == 'DRY' %}warn{% else %}ok{% endif %}">{{ r.soil_status or '—' }}</td>
        <td>{{ r.light_pct or '—' }}</td>
        <td class="{% if r.pump %}warn{% else %}dim{% endif %}">{{ 'ON' if r.pump else 'off' }}</td>
        <td class="{% if r.ok %}ok{% else %}warn{% endif %}">{{ '✓ OK' if r.ok else '⚠ ALERT' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</body>
</html>"""

app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(
        DASHBOARD_HTML,
        latest=db_latest(),
        rows=db_recent(40)
    )

@app.route("/api/latest")
def api_latest():
    return jsonify(db_latest())

@app.route("/api/history")
def api_history():
    return jsonify(db_recent(120))


# ─────────────────────────────────────────────────────────────────
#  DEMO MODE  (no Arduino needed)
# ─────────────────────────────────────────────────────────────────
def demo_mode():
    """Inject synthetic readings so the dashboard works without hardware."""
    import random, math
    print(f"{Y}[Demo] Running in demo mode — no serial port required.{X}")
    step = 0
    while True:
        step += 1
        soil_raw = int(400 + 200 * math.sin(step / 10) + random.gauss(0, 20))
        row = {
            "node": "demo-node-01",
            "sensors": {
                "temperature_c": round(24 + 6 * math.sin(step / 20) + random.gauss(0, 0.3), 1),
                "humidity_pct":  round(55 + 10 * math.sin(step / 15) + random.gauss(0, 1), 1),
                "soil_raw":      soil_raw,
                "soil_status":   "DRY" if soil_raw <= 400 else ("MOIST" if soil_raw <= 600 else "WET"),
                "light_pct":     int(60 + 30 * math.sin(step / 25)),
            },
            "status": {
                "pump":       soil_raw <= 400,
                "soil_alert": soil_raw <= 400,
                "temp_alert": False,
                "ok":         soil_raw > 400,
            }
        }
        with reading_lock:
            global latest_reading
            latest_reading = row
        db_insert_reading(row)
        time.sleep(5)


# ─────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    demo = "--demo" in sys.argv

    print(f"\n{C}{'═'*55}")
    print(f"  🌱  Smart Farm Monitor")
    print(f"  Mode : {'DEMO (synthetic data)' if demo else 'LIVE (Arduino serial)'}")
    print(f"  DB   : {DB_PATH.resolve()}")
    print(f"  Web  : http://0.0.0.0:5000")
    print(f"{'═'*55}{X}\n")

    db_init()

    # Start background thread
    reader_fn = demo_mode if demo else serial_reader
    t = threading.Thread(target=reader_fn, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=5000, debug=False)
