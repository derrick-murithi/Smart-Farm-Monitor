/*
 ╔══════════════════════════════════════════════════════════════════╗
 ║  Smart Farm Monitor — Arduino Sensor Node                       ║
 ║  Reads soil moisture, DHT22 temp/humidity, light (LDR)         ║
 ║  Sends JSON over Serial at 9600 baud every 5 seconds           ║
 ║                                                                  ║
 ║  Wiring:                                                         ║
 ║    DHT22        → Pin 7  (data) + 3.3V + GND                   ║
 ║    Soil sensor  → A0    (analog signal)                         ║
 ║    LDR          → A1    (voltage divider with 10kΩ resistor)    ║
 ║    LED (status) → Pin 13                                        ║
 ║                                                                  ║
 ║  Libraries: DHT sensor library by Adafruit                      ║
 ║  Board    : Arduino Uno / Nano / Mega                           ║
 ╚══════════════════════════════════════════════════════════════════╝
*/

#include <DHT.h>
#include <ArduinoJson.h>

// ── Pin definitions ─────────────────────────────────────────────
#define DHT_PIN        7
#define DHT_TYPE       DHT22
#define SOIL_PIN       A0
#define LIGHT_PIN      A1
#define STATUS_LED     13
#define PUMP_RELAY_PIN 8   // optional: relay for irrigation pump

// ── Thresholds ───────────────────────────────────────────────────
#define SOIL_DRY_THRESHOLD   400   // raw ADC value (<= means DRY)
#define TEMP_HIGH_THRESHOLD  35.0  // °C
#define HUMIDITY_LOW_THRESH  30.0  // %

// ── Timing ───────────────────────────────────────────────────────
#define SAMPLE_INTERVAL_MS  5000   // 5 seconds between readings
#define BLINK_COUNT         3      // LED blinks on alert

DHT dht(DHT_PIN, DHT_TYPE);

unsigned long lastSample = 0;
unsigned int  readingId  = 0;
bool          pumpActive = false;

// ── Setup ────────────────────────────────────────────────────────
void setup() {
  Serial.begin(9600);
  dht.begin();
  pinMode(STATUS_LED,    OUTPUT);
  pinMode(PUMP_RELAY_PIN, OUTPUT);
  digitalWrite(PUMP_RELAY_PIN, LOW);  // pump off by default

  // Boot blink
  for (int i = 0; i < 3; i++) {
    digitalWrite(STATUS_LED, HIGH); delay(150);
    digitalWrite(STATUS_LED, LOW);  delay(150);
  }

  Serial.println(F("{\"event\":\"boot\",\"node\":\"farm-node-01\"}"));
}

// ── Helpers ──────────────────────────────────────────────────────
int readSoilMoisture() {
  // Average 5 readings for stability
  long sum = 0;
  for (int i = 0; i < 5; i++) { sum += analogRead(SOIL_PIN); delay(10); }
  return sum / 5;
}

int readLightLevel() {
  // Map raw ADC (0-1023) to percentage (0-100)
  return map(analogRead(LIGHT_PIN), 0, 1023, 0, 100);
}

String soilStatus(int rawSoil) {
  if (rawSoil <= SOIL_DRY_THRESHOLD)  return "DRY";
  if (rawSoil <= 600)                 return "MOIST";
  return "WET";
}

void blinkAlert(int n) {
  for (int i = 0; i < n; i++) {
    digitalWrite(STATUS_LED, HIGH); delay(80);
    digitalWrite(STATUS_LED, LOW);  delay(80);
  }
}

void controlPump(bool shouldRun) {
  if (shouldRun != pumpActive) {
    pumpActive = shouldRun;
    digitalWrite(PUMP_RELAY_PIN, shouldRun ? HIGH : LOW);
  }
}

// ── Main loop ────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_INTERVAL_MS) return;
  lastSample = now;

  // ── Read sensors ──────────────────────────────────────────────
  float temp   = dht.readTemperature();
  float humid  = dht.readHumidity();
  int   soil   = readSoilMoisture();
  int   light  = readLightLevel();
  bool  dhtOk  = !isnan(temp) && !isnan(humid);

  // ── Alert logic ───────────────────────────────────────────────
  bool soilAlert  = (soil <= SOIL_DRY_THRESHOLD);
  bool tempAlert  = dhtOk && (temp >= TEMP_HIGH_THRESHOLD);
  bool humidAlert = dhtOk && (humid <= HUMIDITY_LOW_THRESH);

  // Auto-irrigate if soil is dry
  controlPump(soilAlert);

  // Blink LED if any alert active
  if (soilAlert || tempAlert || humidAlert) {
    blinkAlert(BLINK_COUNT);
  } else {
    // Normal heartbeat blink
    digitalWrite(STATUS_LED, HIGH); delay(50); digitalWrite(STATUS_LED, LOW);
  }

  // ── Build JSON payload ────────────────────────────────────────
  StaticJsonDocument<256> doc;
  doc["id"]        = ++readingId;
  doc["node"]      = "farm-node-01";
  doc["uptime_s"]  = now / 1000;

  JsonObject sensors = doc.createNestedObject("sensors");
  if (dhtOk) {
    sensors["temperature_c"] = round(temp * 10.0) / 10.0;
    sensors["humidity_pct"]  = round(humid * 10.0) / 10.0;
  } else {
    sensors["temperature_c"] = nullptr;
    sensors["humidity_pct"]  = nullptr;
  }
  sensors["soil_raw"]   = soil;
  sensors["soil_status"]= soilStatus(soil);
  sensors["light_pct"]  = light;

  JsonObject status = doc.createNestedObject("status");
  status["pump"]      = pumpActive;
  status["soil_alert"]= soilAlert;
  status["temp_alert"]= tempAlert;
  status["ok"]        = !(soilAlert || tempAlert || humidAlert);

  // ── Transmit ──────────────────────────────────────────────────
  serializeJson(doc, Serial);
  Serial.println();  // newline delimiter for Python parser
}
