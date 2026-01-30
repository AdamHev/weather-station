import time
import sqlite3
import datetime

import board
import busio
import adafruit_ahtx0

from RPLCD.gpio import CharLCD
import RPi.GPIO as GPIO

import os
import requests
from dotenv import load_dotenv


load_dotenv()
API_KEY = os.getenv("OPENWEATHER_API_KEY")
CITY = os.getenv("WEATHER_CITY", "Satu Mare,RO")
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

# ========================
# Config
# ========================

DB_PATH = os.getenv("DB_PATH", "./aht20_data.sqlite")

LOG_INTERVAL_SECONDS = 10 * 60      # Log to DB every 10 minutes
NORMAL_DISPLAY_SECONDS = 10     # Temp + humidity screen
MESSAGE_DISPLAY_SECONDS = 3     # Mood screen duration
TIME_DISPLAY_SECONDS = 3        # Time screen duration
WEATHER_DISPLAY_SECONDS = 5     # Weather display duration


LAST_WEATHER = None
LAST_WEATHER_TIME = 0
LAST_DESCRIPTION = None
WEATHER_CACHE_SECONDS = 10 * 60

# ========================
# Database helpers
# ========================

def get_db_connection():
    """Create a connection to the SQLite database and ensure table exists."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            temperature_c REAL NOT NULL,
            humidity_percent REAL NOT NULL
        )
        """
    )

    conn.commit()
    return conn


def log_reading(conn, temperature, humidity):
    """Insert one sensor reading into the database."""
    cursor = conn.cursor()
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")

    cursor.execute(
        """
        INSERT INTO readings (timestamp, temperature_c, humidity_percent)
        VALUES (?, ?, ?)
        """,
        (timestamp, temperature, humidity),
    )

    conn.commit()
    print(f"[{timestamp}] Saved: {temperature:.2f} C, {humidity:.2f} %")


# ========================
# Hardware helpers
# ========================

def get_sensor():
    """Initialize the I2C bus and AHT20 sensor."""
    i2c = busio.I2C(board.SCL, board.SDA)
    sensor = adafruit_ahtx0.AHTx0(i2c)
    return sensor


def setup_lcd():
    """Initialize the 16x2 LCD."""
    lcd = CharLCD(
        numbering_mode=GPIO.BCM,
        cols=16,
        rows=2,
        pin_rs=27,
        pin_e=22,
        pins_data=[25, 24, 23, 18],   # D4–D7
        pin_rw=None,
        dotsize=8,
    )
    return lcd


def safe_read_sensor(sensor, retries=3, delay=0.2):
    """
    Read temperature and humidity with a few retries to reduce random OSError(5)s.
    Returns (temperature, humidity) or (None, None) if it fails.
    """
    for attempt in range(retries):
        try:
            temperature = sensor.temperature
            humidity = sensor.relative_humidity
            return temperature, humidity
        except OSError as e:
            print(f"Sensor read error (attempt {attempt + 1}):", repr(e))
            time.sleep(delay)
    return None, None


# ========================
# Comfort label helper
# ========================

def get_comfort_label(temp_c: float) -> str:
    """
    Decide which message to show based on temperature:
      > 23  -> ***TOASTIE***
      21–23 -> ***COZY***
      20–21 -> ***BLANKET TIME***
      < 19  -> ***WINTERFELL***
    """
    if temp_c > 23:
        return "***TOASTIE***"
    elif temp_c > 21:
        return "***COZY***"
    elif temp_c > 20:
        return "**BLANKET TIME**"
    else:
        return "***WINTERFELL***"
    
# ========================
# Time label helper
# ========================


def get_time_label() -> str:
    return datetime.datetime.now().strftime("%H:%M")


# ========================
# Weather label helper
# ========================

def get_weather_label():
    global LAST_WEATHER, LAST_WEATHER_TIME, LAST_DESCRIPTION

    if not API_KEY:
        return "No API key".ljust(16), "".ljust(16)

    now = time.time()
    if (
        LAST_WEATHER is not None
        and LAST_DESCRIPTION is not None
        and (now - LAST_WEATHER_TIME) < WEATHER_CACHE_SECONDS
    ):
        return LAST_WEATHER, LAST_DESCRIPTION

    try:
        response = requests.get(
            BASE_URL,
            params={"q": CITY, "units": "metric", "appid": API_KEY},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()

        temp = data["main"]["temp"]
        wind = data["wind"]["speed"]
        desc = data["weather"][0]["description"]

        line1 = f"T:{temp:.1f}C W:{wind * 3.6:.1f}km/h"
        line1 = line1[:16].ljust(16)

        line2 = desc.capitalize()
        if len(line2) > 16:
            line2 = line2[:13] + "..."
        line2 = line2.ljust(16)

        LAST_WEATHER = line1
        LAST_DESCRIPTION = line2
        LAST_WEATHER_TIME = now

        return line1, line2

    except Exception as e:
        print("Error fetching weather data:", repr(e))
        fallback1 = (LAST_WEATHER or "Weather error").ljust(16)[:16]
        fallback2 = (LAST_DESCRIPTION or "").ljust(16)[:16]
        return fallback1, fallback2


# ========================
# Main loop
# ========================

def main():
    print("Initializing DB, sensor, and LCD...")
    conn = get_db_connection()
    sensor = get_sensor()
    lcd = setup_lcd()

    # Give the sensor a moment to settle
    time.sleep(2)

    print("Starting loop (LCD + logging). Press CTRL+C to stop.")

    last_log_time = 0.0

    try:
        while True:
            # 1) Read sensor ONCE for this whole cycle
            temperature, humidity = safe_read_sensor(sensor)

            if temperature is None or humidity is None:
                lcd.clear()
                lcd.cursor_pos = (0, 0)
                lcd.write_string("Sensor error".ljust(16))
                lcd.cursor_pos = (1, 0)
                lcd.write_string("Check wiring".ljust(16))
                time.sleep(5)
                continue

            # Calibration
            temperature -= 0.9

            # 2) Log to DB if needed (uses same values as display)
            now_wall = time.time()
            if now_wall - last_log_time >= LOG_INTERVAL_SECONDS:
                log_reading(conn, temperature, humidity)
                last_log_time = now_wall

            # ============================
            # 3) NORMAL SCREEN
            # ============================
            lcd.clear()
            line1 = f"Temp: {temperature:4.1f}C"
            line2 = f"Hum:  {humidity:4.1f}%"

            lcd.cursor_pos = (0, 0)
            lcd.write_string(line1.ljust(16))
            lcd.cursor_pos = (1, 0)
            lcd.write_string(line2.ljust(16))

            time.sleep(NORMAL_DISPLAY_SECONDS)

            # ============================
            # 4) MOOD SCREEN
            # ============================
            label = get_comfort_label(temperature)
            msg_line = label.center(16)[:16]

            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string(msg_line)
            lcd.cursor_pos = (1, 0)
            lcd.write_string("".ljust(16))

            time.sleep(MESSAGE_DISPLAY_SECONDS)

            # ============================
            # 4) TIME SCREEN
            # ============================
            time_str = get_time_label()

            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string(time_str.center(16))
            lcd.cursor_pos = (1, 0)
            lcd.write_string("    =^. .^=     ")

            time.sleep(TIME_DISPLAY_SECONDS)


            # ============================
            # 4) Weather Screen(API)
            # ============================
            weather_line1, weather_line2 = get_weather_label()

            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string(weather_line1[:16])
            lcd.cursor_pos = (1, 0)
            lcd.write_string(weather_line2[:16])

            time.sleep(WEATHER_DISPLAY_SECONDS)


    except KeyboardInterrupt:
        print("\nStopped by user (CTRL+C).")

    finally:
        print("Cleaning up...")
        lcd.clear()
        GPIO.cleanup()
        conn.close()
        print("Database connection closed.")


if __name__ == "__main__":
    main()