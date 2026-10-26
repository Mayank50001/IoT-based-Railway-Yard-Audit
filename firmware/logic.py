"""Portable decision logic for the ESP32 lane firmware.

This module imports only ``json``, ``os`` and ``time`` so the same code runs on
MicroPython and under CPython. That lets the rules that are easiest to get
wrong — passage hysteresis, missing-echo handling, per-UID cooldown, event ids,
retry backoff, and the on-flash queue — be tested without hardware.

It contains no pin, Wi-Fi, NTP or HTTP access. ``main.py`` owns those.
"""

import json
import os
import time


# ``time.ticks_ms()`` is a 30-bit counter on MicroPython and wraps roughly every
# 12 days. Comparisons must survive that wrap, so all interval arithmetic goes
# through the helpers below instead of raw subtraction.
TICKS_PERIOD = 1 << 30
TICKS_MASK = TICKS_PERIOD - 1
TICKS_HALF = TICKS_PERIOD >> 1


def elapsed_ms(now, then):
    """Signed milliseconds from ``then`` to ``now``, wrap-safe."""
    return ((now - then + TICKS_HALF) & TICKS_MASK) - TICKS_HALF


def ticks_add(now, delta_ms):
    """Add a delay to a ticks value, wrapping inside the same period."""
    return (now + delta_ms) & TICKS_MASK


def iso_utc(epoch_seconds):
    """Format epoch seconds as ``YYYY-MM-DDTHH:MM:SS.mmmZ`` in UTC.

    ``time.gmtime`` is used rather than ``time.localtime`` so a device whose
    timezone was changed never reports local time as UTC.
    """
    whole = int(epoch_seconds)
    millis = int(round((epoch_seconds - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis -= 1000
    elif millis < 0:
        whole -= 1
        millis += 1000
    stamp = time.gmtime(whole)
    return "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ" % (
        stamp[0],
        stamp[1],
        stamp[2],
        stamp[3],
        stamp[4],
        stamp[5],
        millis,
    )


def make_event_id(device_id, boot_id, sequence):
    """Build the stable ``<device_id>-<boot_id>-<sequence>`` event id."""
    return "%s-%s-%06d" % (device_id, boot_id, int(sequence))


def backoff_seconds(attempt, cap=30):
    """Exponential retry delay, capped so a demo is not stalled for minutes."""
    if attempt <= 0:
        return 1
    exponent = attempt
    if exponent > 5:
        exponent = 5
    delay = 1 << exponent
    if delay > cap:
        return cap
    return delay


class UidGate:
    """Per-UID presence gate with a cooldown.

    The first successful read of a UID emits one event. Every later read of the
    same UID refreshes its last-seen time without emitting, so a tag resting on
    the reader is one observation rather than a stream. The UID emits again only
    after it has been absent for ``cooldown_ms``.

    State is per UID rather than "last UID only": alternating two tags on the
    bench must not make the first tag look new every time the second one is
    read. The backend counts distinct UIDs, so extra observations are not
    harmful, but they are noise.
    """

    def __init__(self, cooldown_ms=3000, max_tracked=32):
        self.cooldown_ms = int(cooldown_ms)
        self.max_tracked = int(max_tracked)
        self.last_seen = {}

    def observe(self, uid, now_ms):
        """Record a raw read and return True when it should become an event."""
        then = self.last_seen.get(uid)
        emit = then is None or elapsed_ms(now_ms, then) >= self.cooldown_ms
        self.last_seen[uid] = now_ms
        if len(self.last_seen) > self.max_tracked:
            self._prune(now_ms)
        return emit

    def _prune(self, now_ms):
        # Newest first, so the tail is what has been absent longest.
        ordered = sorted(
            self.last_seen.items(),
            key=lambda item: elapsed_ms(now_ms, item[1]),
        )
        for uid, _ in ordered[self.max_tracked :]:
            del self.last_seen[uid]


class PassageDetector:
    """Ultrasonic presence detector with hysteresis and confirmation.

    ``enter_cm`` and ``exit_cm`` form a hysteresis band: a reading must be
    closer than ``enter_cm`` to start a passage, and farther than ``exit_cm`` to
    end it. ``occupied_samples`` and ``clear_samples`` reject single-sample
    glitches in both directions.

    A missing echo (``None``) is never treated as clear while the zone is
    occupied. A dead, blocked, or saturated sensor must not fabricate an exit
    and split one vehicle into two passages.
    """

    def __init__(
        self,
        enter_cm,
        exit_cm,
        occupied_samples=3,
        clear_samples=4,
        min_occupied_ms=120,
    ):
        if float(exit_cm) <= float(enter_cm):
            raise ValueError("exit_cm must be greater than enter_cm")
        self.enter_cm = float(enter_cm)
        self.exit_cm = float(exit_cm)
        self.occupied_samples = int(occupied_samples)
        self.clear_samples = int(clear_samples)
        self.min_occupied_ms = int(min_occupied_ms)
        self.reset()

    def reset(self):
        self.occupied = False
        self.entry_ms = None
        self.pending_entry_ms = None
        self.pending_clear_ms = None
        self.occupied_count = 0
        self.clear_count = 0
        self.min_cm = None
        self.max_cm = None

    def occupied_ms(self, now_ms):
        if not self.occupied or self.entry_ms is None:
            return 0
        value = elapsed_ms(now_ms, self.entry_ms)
        return value if value > 0 else 0

    def observe(self, distance_cm, now_ms):
        """Feed one reading; return a completed passage dict or None."""
        if self.occupied:
            return self._while_occupied(distance_cm, now_ms)
        return self._while_clear(distance_cm, now_ms)

    def _while_clear(self, distance_cm, now_ms):
        if distance_cm is None or distance_cm >= self.enter_cm:
            self.occupied_count = 0
            self.pending_entry_ms = None
            return None

        if self.occupied_count == 0:
            self.pending_entry_ms = now_ms
        self.occupied_count += 1
        if self.occupied_count < self.occupied_samples:
            return None

        self.occupied = True
        self.entry_ms = self.pending_entry_ms
        self.min_cm = distance_cm
        self.max_cm = distance_cm
        self.clear_count = 0
        self.pending_clear_ms = None
        return None

    def _while_occupied(self, distance_cm, now_ms):
        if distance_cm is None:
            # Missing echo tells us nothing about the zone. Keep the passage
            # open and restart the clear confirmation.
            self.clear_count = 0
            self.pending_clear_ms = None
            return None

        if distance_cm < self.min_cm:
            self.min_cm = distance_cm
        if distance_cm > self.max_cm:
            self.max_cm = distance_cm

        if distance_cm > self.exit_cm:
            if self.clear_count == 0:
                self.pending_clear_ms = now_ms
            self.clear_count += 1
            if self.clear_count >= self.clear_samples:
                return self._finish()
            return None

        self.clear_count = 0
        self.pending_clear_ms = None
        return None

    def _finish(self):
        duration = elapsed_ms(self.pending_clear_ms, self.entry_ms)
        passage = {
            "started_ms": self.entry_ms,
            "ended_ms": self.pending_clear_ms,
            "duration_ms": duration,
            "min_cm": int(round(self.min_cm)),
            "max_cm": int(round(self.max_cm)),
        }
        self.reset()
        if duration < self.min_occupied_ms or duration < 0:
            return None
        return passage


class EventQueue:
    """One file per pending event, named by sequence.

    A file-per-event layout is crash-safe without a database: a partially
    written record has a ``.tmp`` suffix and is discarded at load. The event id
    is assigned when the event is enqueued and stored inside the file, so an
    event queued before a reboot keeps its identifier even though the firmware
    generates a new boot id at start.

    The queue is bounded. When it overflows the oldest records are dropped and
    counted, because the newest events describe the train currently at the lane
    and are more useful during a demo than the beginning of an outage.
    """

    def __init__(self, directory, device_id, boot_id, max_events=2000):
        self.directory = directory.rstrip("/") or "/"
        self.device_id = device_id
        self.boot_id = boot_id
        self.max_events = int(max_events)
        self.entries = []
        self.dropped = 0
        self.next_seq = 1
        self.last_seq = 0
        self._ensure_directory()
        self._load()

    def _ensure_directory(self):
        try:
            os.mkdir(self.directory)
        except OSError:
            pass

    def _path(self, sequence):
        return "%s/%08d.json" % (self.directory, int(sequence))

    def _load(self):
        try:
            names = os.listdir(self.directory)
        except OSError:
            names = []

        entries = []
        for name in names:
            if name.endswith(".tmp"):
                self._remove("%s/%s" % (self.directory, name))
                continue
            if not name.endswith(".json"):
                continue

            stem = name[:-5]
            if not stem.isdigit():
                continue

            path = "%s/%s" % (self.directory, name)
            try:
                with open(path) as handle:
                    json.loads(handle.read())
            except (OSError, ValueError):
                # An unreadable record would block every batch behind it.
                self._remove(path)
                continue
            entries.append((int(stem), path))

        entries.sort()
        self.entries = entries
        self.next_seq = entries[-1][0] + 1 if entries else 1
        self.last_seq = entries[-1][0] if entries else 0

    def count(self):
        return len(self.entries)

    def append(self, event):
        """Persist one event and return the record with id and sequence."""
        sequence = self.next_seq
        record = dict(event)
        record["sequence"] = sequence
        record["event_id"] = make_event_id(
            self.device_id, self.boot_id, sequence
        )
        path = self._path(sequence)
        temporary = path + ".tmp"
        with open(temporary, "w") as handle:
            handle.write(json.dumps(record))
        os.rename(temporary, path)

        self.entries.append((sequence, path))
        self.next_seq = sequence + 1
        self.last_seq = sequence
        self._enforce_limit()
        return record

    def batch(self, limit):
        """Return up to ``limit`` ``(path, event)`` pairs, oldest first."""
        result = []
        unusable = []
        for _, path in self.entries[: int(limit)]:
            try:
                with open(path) as handle:
                    result.append((path, json.loads(handle.read())))
            except (OSError, ValueError):
                unusable.append(path)
        if unusable:
            self.drop(unusable)
        return result

    def drop(self, paths):
        """Forget and remove stored events by path."""
        unwanted = {}
        for path in paths:
            unwanted[path] = True
            self._remove(path)
        if not unwanted:
            return
        kept = []
        for sequence, path in self.entries:
            if path not in unwanted:
                kept.append((sequence, path))
        self.entries = kept

    def _enforce_limit(self):
        while len(self.entries) > self.max_events:
            _, path = self.entries.pop(0)
            self._remove(path)
            self.dropped += 1

    def _remove(self, path):
        try:
            os.remove(path)
        except OSError:
            pass
