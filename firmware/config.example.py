# Copy this file to config.py and edit it on your machine before copying it to
# the ESP32. config.py is gitignored and must never be committed.
#
#   cp firmware/config.example.py firmware/config.py

# --- Wi-Fi -----------------------------------------------------------------

WIFI_SSID = "your-wifi-name"
# Leave empty for an open network.
WIFI_PASSWORD = "your-wifi-password"

# --- Backend ---------------------------------------------------------------
#
# Use the laptop's LAN address, not 127.0.0.1: the ESP32 is a different host.
# Start the server with:
#   ../env/bin/python manage.py runserver 0.0.0.0:8000
# and add the same address to DJANGO_ALLOWED_HOSTS.
#
# The path is appended by main.py: /api/ingest/events
API_BASE = "http://192.168.1.50:8000"

# Must match the Device row in the backend. The demo seed creates "gate-1".
DEVICE_ID = "gate-1"

# Printed once by:
#   ../env/bin/python manage.py seed_demo --reset --rotate-key
# The key is stored hashed on the server; it cannot be recovered afterwards.
API_KEY = "paste-the-generated-key-here"

# --- SPI / RC522 -----------------------------------------------------------
#
# Matches the bench wiring. The RC522 is a 3.3 V part: 3.3 V supply, 3.3 V SPI.
SPI_ID = 2
SPI_BAUD = 1_000_000
PIN_SCK = 18
PIN_MOSI = 23
PIN_MISO = 19
PIN_CS = 5
PIN_RST = 22

# --- Ultrasonic ------------------------------------------------------------
#
# HC-SR04 on the bench. Echo is driven at ~5 V by the module; GPIO 25 is a
# 3.3 V pin. It has worked in this build, but a 1 k series + 2 k to ground
# divider on Echo removes the out-of-spec condition.
PIN_TRIG = 26
PIN_ECHO = 25
ECHO_TIMEOUT_US = 30000

# Distance below which the zone is treated as occupied, in centimetres.
# PASSAGE_EXIT_CM must be larger: it is the hysteresis band that stops a
# reading hovering at the threshold from chattering.
#
# 140 cm matches the bench geometry. Measure the resting distance with no
# object present and keep PASSAGE_EXIT_CM comfortably below it.
PASSAGE_ENTER_CM = 140.0
PASSAGE_EXIT_CM = 150.0

# Consecutive samples required in each direction. At a 20 ms loop this is
# roughly 60 ms to enter and 80 ms to leave.
OCCUPIED_CONFIRM_SAMPLES = 3
CLEAR_CONFIRM_SAMPLES = 4

# A passage shorter than this is treated as a glitch and not reported.
MIN_OCCUPIED_MS = 120

# A passage still open after this long is assumed to be a stuck object or a
# dead sensor. It is discarded and the detector resets; it is not reported as
# a vehicle. Missing echo alone never closes a passage.
MAX_PASSAGE_MS = 30000

# --- RFID ------------------------------------------------------------------

# The same UID emits one event and then stays quiet until it has been absent
# for this long. Every successful raw read refreshes the timer.
RFID_COOLDOWN_MS = 3000

# --- Queue / transport -----------------------------------------------------

QUEUE_DIR = "/queue"
QUEUE_MAX_EVENTS = 2000
BATCH_SIZE = 25
HTTP_TIMEOUT_S = 4
LOOP_MS = 20
STATUS_EVERY_MS = 60000

# NTP resync interval once the clock is set. A demo run does not need more
# than the boot sync; this only matters for a long-running unit.
CLOCK_RESYNC_MS = 6 * 60 * 60 * 1000
