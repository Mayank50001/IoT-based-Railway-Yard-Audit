"""ESP32 lane firmware for the railway yard RFID audit prototype.

The device senses and reports. It does not decide direction, plans, counts,
inventory, validation, or movement boundaries — the backend owns those. It
therefore emits two independent kinds of evidence and never pairs them:

* ``RFID_DETECTED`` when a UID appears and the per-UID cooldown has elapsed;
* ``PHYSICAL_PASSAGE`` when the ultrasonic detection zone is interrupted and
  then clears.

A passage is advisory evidence. Forcing a card read into a passage would hide
a missed locomotive read, which is exactly what v4 requires the backend to
report rather than fabricate.

Run this file as ``main.py`` on the device. ``boot.py`` is not modified.
"""

import json
import os
import socket
import time

import network
from machine import Pin, SPI

import logic
import mfrc522

try:
    import ntptime
except ImportError:  # pragma: no cover - depends on the firmware build
    ntptime = None

try:
    import ssl
except ImportError:  # pragma: no cover - HTTPS is optional
    ssl = None

try:
    import config
except ImportError:
    raise SystemExit(
        "config.py not found. Copy config.example.py to config.py and edit it."
    )


def _cfg(name, default=None, required=False):
    value = getattr(config, name, default)
    if required and value in (None, ""):
        raise SystemExit("config.py is missing a value for %s" % name)
    return value


# --- HTTP ------------------------------------------------------------------


class Endpoint:
    """Parsed API base URL such as ``http://192.168.1.50:8000``."""

    def __init__(self, base_url):
        text = base_url.strip()
        if "://" not in text:
            raise ValueError("API_BASE must start with http:// or https://")
        scheme, rest = text.split("://", 1)
        self.tls = scheme.lower() == "https"

        if "/" in rest:
            authority, prefix = rest.split("/", 1)
            prefix = prefix.strip("/")
            self.prefix = ("/" + prefix) if prefix else ""
        else:
            authority = rest
            self.prefix = ""

        if ":" in authority:
            host, port = authority.rsplit(":", 1)
            self.port = int(port)
        else:
            host = authority
            self.port = 443 if self.tls else 80
        self.host = host


def _connect(endpoint, timeout):
    address = socket.getaddrinfo(endpoint.host, endpoint.port)[0][-1]
    sock = socket.socket()
    sock.settimeout(timeout)
    sock.connect(address)
    if endpoint.tls:
        if ssl is None:
            raise OSError("HTTPS requested but this firmware has no ssl module")
        try:
            sock = ssl.wrap_socket(sock, server_hostname=endpoint.host)
        except TypeError:
            sock = ssl.wrap_socket(sock)
    return sock


def _send_all(sock, data):
    sent = 0
    total = len(data)
    while sent < total:
        count = sock.send(data[sent:])
        if not count:
            raise OSError("socket closed while sending")
        sent += count


def _read_response(sock, header_limit=8192):
    """Read one HTTP/1.1 response, honouring Content-Length when present."""
    data = b""
    marker = b"\r\n\r\n"
    while True:
        index = data.find(marker)
        if index >= 0:
            break
        if len(data) > header_limit:
            raise OSError("response headers too large")
        chunk = sock.recv(256)
        if not chunk:
            raise OSError("connection closed before response headers")
        data += chunk

    head = data[:index]
    body = data[index + len(marker) :]
    lines = head.split(b"\r\n")

    status = 0
    if lines:
        parts = lines[0].split(b" ")
        if len(parts) >= 2:
            try:
                status = int(parts[1])
            except ValueError:
                status = 0

    length = None
    for line in lines[1:]:
        if line.lower().startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                length = None
            break

    if length is not None:
        while len(body) < length:
            chunk = sock.recv(512)
            if not chunk:
                break
            body += chunk
        body = body[:length]
    else:
        while True:
            chunk = sock.recv(512)
            if not chunk:
                break
            body += chunk

    return status, body


def http_post_json(endpoint, path, payload, headers, timeout):
    """POST JSON and return ``(status, body_bytes)``; status 0 means no reply."""
    body = json.dumps(payload).encode()
    lines = [
        "POST %s HTTP/1.1" % path,
        "Host: %s" % endpoint.host,
        "Content-Type: application/json",
        "Content-Length: %d" % len(body),
        "Connection: close",
    ]
    for name in headers:
        lines.append("%s: %s" % (name, headers[name]))
    head = ("\r\n".join(lines) + "\r\n\r\n").encode()

    sock = None
    try:
        sock = _connect(endpoint, timeout)
        _send_all(sock, head)
        _send_all(sock, body)
        return _read_response(sock)
    except Exception as exc:
        return 0, str(exc).encode()
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _parse_json(data):
    try:
        return json.loads(data.decode())
    except Exception:
        return None


def _short(data, limit=120):
    if not data:
        return ""
    try:
        text = data.decode()
    except Exception:
        text = str(data)
    text = text.replace("\n", " ").replace("\r", " ")
    return text[:limit]


# --- Device pieces ---------------------------------------------------------


class Wifi:
    """Non-blocking Wi-Fi association.

    ``connect`` returns immediately on the ESP32; the loop polls
    ``isconnected``. A failed association is retried every 15 seconds, and
    sensing/queueing continues while the radio is down.
    """

    def __init__(self, ssid, password):
        self.ssid = ssid
        self.password = password
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        try:
            # Disable power save: it adds latency to short HTTP requests.
            self.wlan.config(pm=0xA11140)
        except Exception:
            pass
        self.last_attempt = None
        self.connected = False

    def tick(self, now_ms):
        self.connected = self.wlan.isconnected()
        if self.connected:
            return True
        if (
            self.last_attempt is not None
            and logic.elapsed_ms(now_ms, self.last_attempt) < 15000
        ):
            return False
        self.last_attempt = now_ms
        try:
            self.wlan.connect(self.ssid, self.password)
            print("wifi: connecting to %s" % self.ssid)
        except Exception as exc:
            print("wifi: connect failed: %s" % exc)
        return False


class UtcClock:
    """NTP-backed UTC clock that never claims time it does not have."""

    def __init__(self, resync_ms):
        self.resync_ms = int(resync_ms)
        self.synced = False
        self.last_sync = None
        self.last_attempt = None

    def iso(self, offset_seconds=0.0):
        if not self.synced:
            return None
        return logic.iso_utc(time.time() - offset_seconds)

    def tick(self, now_ms, online, busy):
        if ntptime is None or not online:
            return
        if self.synced:
            if (
                self.last_sync is not None
                and logic.elapsed_ms(now_ms, self.last_sync) < self.resync_ms
            ):
                return
        else:
            if (
                self.last_attempt is not None
                and logic.elapsed_ms(now_ms, self.last_attempt) < 60000
            ):
                return
        # NTP can block for a second or more. Do it only while no passage is
        # in progress so a sync cannot swallow a tag read.
        if busy:
            return

        self.last_attempt = now_ms
        try:
            ntptime.settime()
            self.synced = True
            self.last_sync = now_ms
            print("clock: UTC synced at %s" % logic.iso_utc(time.time()))
        except Exception as exc:
            print("clock: sync failed: %s" % exc)


class Ultrasonic:
    """HC-SR04 trigger/echo reader returning centimetres or None."""

    def __init__(self, trig_pin, echo_pin, timeout_us):
        self.trig = Pin(trig_pin, Pin.OUT)
        self.echo = Pin(echo_pin, Pin.IN)
        self.timeout_us = int(timeout_us)

    def read_cm(self):
        trig = self.trig
        echo = self.echo

        trig.value(0)
        time.sleep_us(5)
        trig.value(1)
        time.sleep_us(10)
        trig.value(0)

        start = time.ticks_us()
        while echo.value() == 0:
            if time.ticks_diff(time.ticks_us(), start) > self.timeout_us:
                return None
        pulse_start = time.ticks_us()
        while echo.value() == 1:
            if time.ticks_diff(time.ticks_us(), start) > self.timeout_us:
                return None
        pulse_end = time.ticks_us()

        duration = time.ticks_diff(pulse_end, pulse_start)
        distance = (duration * 0.0343) / 2.0
        if distance <= 0 or distance > 400:
            return None
        return distance


def _boot_id():
    try:
        return "".join("%02x" % value for value in os.urandom(3))
    except Exception:
        return "%06x" % (time.ticks_ms() & 0xFFFFFF)


# --- Lane ------------------------------------------------------------------


class Lane:
    def __init__(self):
        self.device_id = _cfg("DEVICE_ID", required=True)
        if len(self.device_id) > 50:
            raise SystemExit("DEVICE_ID must be at most 50 characters")

        self.api_key = _cfg("API_KEY", required=True)
        self.endpoint = Endpoint(_cfg("API_BASE", required=True))

        self.spi = SPI(
            _cfg("SPI_ID", 2),
            baudrate=_cfg("SPI_BAUD", 1000000),
            polarity=0,
            phase=0,
            sck=Pin(_cfg("PIN_SCK", 18)),
            mosi=Pin(_cfg("PIN_MOSI", 23)),
            miso=Pin(_cfg("PIN_MISO", 19)),
        )
        self.reader = mfrc522.MFRC522(
            self.spi,
            Pin(_cfg("PIN_RST", 22), Pin.OUT),
            Pin(_cfg("PIN_CS", 5), Pin.OUT),
        )
        self.sonic = Ultrasonic(
            _cfg("PIN_TRIG", 26),
            _cfg("PIN_ECHO", 25),
            _cfg("ECHO_TIMEOUT_US", 30000),
        )

        self.uid_gate = logic.UidGate(_cfg("RFID_COOLDOWN_MS", 3000))
        self.detector = logic.PassageDetector(
            _cfg("PASSAGE_ENTER_CM", 140.0),
            _cfg("PASSAGE_EXIT_CM", 150.0),
            _cfg("OCCUPIED_CONFIRM_SAMPLES", 3),
            _cfg("CLEAR_CONFIRM_SAMPLES", 4),
            _cfg("MIN_OCCUPIED_MS", 120),
        )
        self.queue = logic.EventQueue(
            _cfg("QUEUE_DIR", "/queue"),
            self.device_id,
            _boot_id(),
            _cfg("QUEUE_MAX_EVENTS", 2000),
        )

        self.wifi = Wifi(
            _cfg("WIFI_SSID", required=True),
            _cfg("WIFI_PASSWORD", ""),
        )
        self.clock = UtcClock(_cfg("CLOCK_RESYNC_MS", 6 * 60 * 60 * 1000))

        self.batch_size = int(_cfg("BATCH_SIZE", 25))
        self.http_timeout = int(_cfg("HTTP_TIMEOUT_S", 4))
        self.loop_ms = int(_cfg("LOOP_MS", 20))
        self.max_passage_ms = int(_cfg("MAX_PASSAGE_MS", 30000))
        self.status_every_ms = int(_cfg("STATUS_EVERY_MS", 60000))

        self.attempt = 0
        self.mode_warned = False
        self.auth_warned = False
        self.next_send = time.ticks_ms()
        self.last_status = time.ticks_ms()

    # --- queueing ----------------------------------------------------------

    def _queue(self, event):
        try:
            return self.queue.append(event)
        except OSError as exc:
            print("queue: write failed: %s" % exc)
            return None

    def _queue_rfid(self, uid):
        event = {
            "event_type": "RFID_DETECTED",
            "device_id": self.device_id,
            "rfid_uid": uid,
        }
        stamp = self.clock.iso()
        if stamp:
            event["timestamp"] = stamp
        return self._queue(event)

    def _queue_passage(self, passage):
        duration = passage["duration_ms"]
        event = {
            "event_type": "PHYSICAL_PASSAGE",
            "device_id": self.device_id,
            "metadata": {
                "duration_ms": duration,
                "min_distance_cm": passage["min_cm"],
                "peak_distance_cm": passage["max_cm"],
            },
        }
        # v4 defines the passage event timestamp as detection-zone entry, even
        # though the event is emitted when the zone clears.
        started = self.clock.iso(duration / 1000.0)
        ended = self.clock.iso()
        if started and ended:
            event["timestamp"] = started
            event["metadata"]["started_at"] = started
            event["metadata"]["ended_at"] = ended
        return self._queue(event)

    # --- transport ---------------------------------------------------------

    def _send(self):
        items = self.queue.batch(self.batch_size)
        if not items:
            # Only unreadable records were left; they were dropped by batch().
            self.next_send = logic.ticks_add(time.ticks_ms(), 5000)
            return

        status, body = http_post_json(
            self.endpoint,
            self.endpoint.prefix + "/api/ingest/events",
            {"events": [event for _, event in items]},
            {"X-Device-ID": self.device_id, "X-Api-Key": self.api_key},
            self.http_timeout,
        )
        now = time.ticks_ms()

        if status == 200:
            self._accept(items, body, now)
            return

        self._note_failure(status, body, now)

    def _accept(self, items, body, now):
        payload = _parse_json(body)
        results = payload.get("results") if isinstance(payload, dict) else None

        if not isinstance(results, list):
            # The server answered 200 but did not describe the batch. Keeping
            # the events is safe: event ids are idempotent on retry.
            print("ingest: 200 without per-event results; retrying later")
            self._retry(now, 2)
            return

        statuses = {}
        rejected = []
        for result in results:
            if not isinstance(result, dict):
                continue
            statuses[result.get("event_id")] = result.get("status")
            if result.get("status") == "rejected":
                rejected.append(result)

        done = []
        for path, event in items:
            if statuses.get(event.get("event_id")) in (
                "accepted",
                "duplicate",
                "rejected",
            ):
                done.append(path)

        if done:
            self.queue.drop(done)

        for result in rejected:
            error = result.get("error") or {}
            print(
                "ingest: rejected %s (%s) %s"
                % (
                    result.get("event_id"),
                    error.get("code"),
                    error.get("message"),
                )
            )

        if done:
            print(
                "ingest: %d stored, %d rejected, %d queued"
                % (
                    len(done) - len(rejected),
                    len(rejected),
                    self.queue.count(),
                )
            )

        self.attempt = 0
        self.mode_warned = False
        self.auth_warned = False

        if len(done) < len(items):
            # A partial result is unexpected. The remainder stays queued and
            # is retried after a short pause rather than spinning.
            self._retry(now, 1)
        else:
            self.next_send = now

    def _retry(self, now, increment):
        self.attempt += increment
        delay = logic.backoff_seconds(self.attempt) * 1000
        self.next_send = logic.ticks_add(now, delay)

    def _note_failure(self, status, body, now):
        snippet = _short(body)

        if status == 409:
            if not self.mode_warned:
                print(
                    "ingest: lane mode is not set (409 LANE_MODE_NOT_SET); "
                    "events retained. Set ENTRY or EXIT on the dashboard."
                )
                self.mode_warned = True
            delay = 5000
        elif status in (401, 403):
            if not self.auth_warned:
                print(
                    "ingest: %d %s; events retained. Check DEVICE_ID, API_KEY, "
                    "and the lane/station state." % (status, snippet)
                )
                self.auth_warned = True
            delay = 60000
        elif status in (400, 413, 422):
            print(
                "ingest: request rejected (%d): %s; events retained."
                % (status, snippet)
            )
            delay = 30000
        elif status == 429:
            print("ingest: rate limited; events retained.")
            delay = 30000
        else:
            self.attempt += 1
            delay = logic.backoff_seconds(self.attempt) * 1000
            print(
                "ingest: transport error %d (%s); retry in %d ms, %d queued."
                % (status, snippet, delay, self.queue.count())
            )

        self.next_send = logic.ticks_add(now, delay)

    # --- main loop ---------------------------------------------------------

    def run(self):
        print(
            "lane %s starting: queue=%d batch=%d pins trigger=%d echo=%d"
            % (
                self.device_id,
                self.queue.count(),
                self.batch_size,
                _cfg("PIN_TRIG", 26),
                _cfg("PIN_ECHO", 25),
            )
        )
        self.wifi.tick(time.ticks_ms())

        while True:
            now = time.ticks_ms()
            online = self.wifi.tick(now)
            self.clock.tick(now, online, self.detector.occupied)

            uid = self.reader.read_uid()
            if uid and self.uid_gate.observe(uid, now):
                record = self._queue_rfid(uid)
                if record:
                    print("rfid: %s queued as %s" % (uid, record["event_id"]))

            distance = self.sonic.read_cm()
            passage = self.detector.observe(distance, now)
            if passage is not None:
                record = self._queue_passage(passage)
                if record:
                    print(
                        "passage: %d ms (min %d cm) queued as %s"
                        % (
                            passage["duration_ms"],
                            passage["min_cm"],
                            record["event_id"],
                        )
                    )
            elif (
                self.detector.occupied
                and self.detector.occupied_ms(now) > self.max_passage_ms
            ):
                print(
                    "passage: open longer than %d ms; discarded and reset"
                    % self.max_passage_ms
                )
                self.detector.reset()

            if self.queue.count() and logic.elapsed_ms(now, self.next_send) >= 0:
                self._send()
                now = time.ticks_ms()

            if logic.elapsed_ms(now, self.last_status) >= self.status_every_ms:
                self.last_status = now
                print(
                    "status: wifi=%s clock=%s queue=%d dropped=%d"
                    % (
                        "up" if self.wifi.connected else "down",
                        "utc" if self.clock.synced else "unsynced",
                        self.queue.count(),
                        self.queue.dropped,
                    )
                )

            time.sleep_ms(self.loop_ms)


def main():
    Lane().run()


if __name__ == "__main__":
    main()
