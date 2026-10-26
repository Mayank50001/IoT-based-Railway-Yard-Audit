# Lane firmware (ESP32 + RC522 + HC-SR04)

Firmware for one yard gate lane. It senses and reports; it does not decide.

The device produces two **independent** kinds of evidence and never pairs them:

| Event | Emitted when |
| --- | --- |
| `RFID_DETECTED` | A tag UID is read and its per-UID cooldown has elapsed. |
| `PHYSICAL_PASSAGE` | The ultrasonic zone becomes occupied, then clears. |

The backend buckets events into movements, applies the lane direction, matches
plans, validates against distinct RFID UIDs and updates inventory. Passage
counts are advisory: a passage count that disagrees with the number of distinct
tags raises `ULTRASONIC_COUNT_MISMATCH` as a warning, never a failure. This is
why the firmware must not invent a card read to "complete" a passage, and must
not merge a card read into a passage. A missed locomotive read has to surface
as `MISSING_LOCOMOTIVE_RFID`, which is only possible if the two sensor streams
stay separate.

## Files

| File | Purpose | Copy to device |
| --- | --- | --- |
| `main.py` | Wi-Fi, NTP, HTTP, event queue, main loop | yes |
| `logic.py` | Testable rules: hysteresis, UID gate, queue, backoff | yes |
| `mfrc522.py` | Minimal RC522 driver (`read_uid`) | yes |
| `config.example.py` | Template for the gitignored `config.py` | as `config.py` |
| `tests/test_logic.py` | 29 CPython tests for `logic.py` | **no** |
| `README.md` | This file | no |

`config.py` holds Wi-Fi credentials and the API key. It is gitignored. Never
commit it.

## Wiring

Confirmed against the working bench build.

### RC522 (3.3 V only)

| RC522 | ESP32 |
| --- | --- |
| SDA / SS | GPIO 5 |
| SCK | GPIO 18 |
| MOSI | GPIO 23 |
| MISO | GPIO 19 |
| RST | GPIO 22 |
| 3.3V | 3.3V |
| GND | GND |

SPI bus 2 at 1 MHz. The RC522 is a 3.3 V part: do not power it from 5 V and do
not pull its SPI lines to 5 V.

### HC-SR04

| HC-SR04 | ESP32 |
| --- | --- |
| VCC | 5V (VIN) |
| TRIG | GPIO 26 |
| GND | GND |
| ECHO | GPIO 25 |

GPIO 26 and 25 were chosen because neither is a strapping pin, so a boot-time
trigger pulse cannot change the flash configuration.

**One known out-of-spec item:** the HC-SR04 drives ECHO at about 5 V, and GPIO
25 is not 5 V tolerant. This build has run that way for a long time without a
failure, so the firmware ships with it. If you want the pin inside spec, put a
divider on ECHO — 1 kΩ in series and 2 kΩ to ground — and leave the rest
unchanged. Expect roughly 3.3 V on the pin afterwards; the firmware does not
care about the exact level, only the pulse width.

## Host setup

The ESP32 is a separate host, so `127.0.0.1` will not work. Use the laptop's LAN
address.

1. Find the laptop's LAN address (example `192.168.1.50`):

   ```bash
   ip route get 1.1.1.1 | awk '{print $7; exit}'
   ```

2. Start the backend listening on all interfaces:

   ```bash
   cd backend
   DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost,192.168.1.50" \
     ../env/bin/python manage.py runserver 0.0.0.0:8000
   ```

   Without the address in `DJANGO_ALLOWED_HOSTS`, Django answers `400` and the
   firmware will treat it as a transport error and keep retrying.

3. Seed the demo lane and get a key:

   ```bash
   ../env/bin/python manage.py seed_demo --reset --rotate-key
   ```

   It prints the key once, like this:

   ```
     Device API key (generated, shown once): <key>
   ```

   The key is stored hashed; if you lose it, run `--rotate-key` again.

4. Put the ESP32 on the same network as the laptop.

### Firewall

If the laptop firewall blocks inbound `8000`, the device will fail to connect
and log `ingest: transport error 0`. Allow the port on the LAN interface only.

## Configure

```bash
cp firmware/config.example.py firmware/config.py
```

Edit `firmware/config.py`:

- `WIFI_SSID`, `WIFI_PASSWORD`
- `API_BASE` — `http://<laptop-lan-ip>:8000`
- `DEVICE_ID` — `gate-1` for the demo seed
- `API_KEY` — the key printed by `seed_demo`

Do not change the pin numbers unless you rewire. The passage thresholds
(`PASSAGE_ENTER_CM`, `PASSAGE_EXIT_CM`) are geometry-dependent; see
[Tuning](#tuning).

## Flash and run

Any of `mpremote`, `ampy`, or Thonny works. With `mpremote`:

```bash
cd firmware
mpremote connect /dev/ttyUSB0 cp main.py :main.py
mpremote connect /dev/ttyUSB0 cp logic.py :logic.py
mpremote connect /dev/ttyUSB0 cp mfrc522.py :mfrc522.py
mpremote connect /dev/ttyUSB0 cp config.py :config.py
```

Do not copy `tests/` or this README to the device.

Start it:

```bash
mpremote connect /dev/ttyUSB0 repl
```

then press `Ctrl-D` (soft reboot), or run:

```python
import main
```

A start banner confirms the configuration:

```
lane gate-1 starting: queue=0 batch=25 pins trigger=26 echo=25
wifi: connecting to <ssid>
clock: UTC synced at 2026-10-25T18:58:57.000Z
```

On the first card:

```
rfid: FA37E63F queued as gate-1-3f9a1c-000001
ingest: 1 stored, 0 rejected, 0 queued
```

## What the firmware expects from the backend

- `POST /api/ingest/events` with `X-Device-ID` and `X-Api-Key`.
- `200` with a `results` array. `accepted`, `duplicate` and `rejected` are all
  terminal: the queued file is deleted. `duplicate` is normal after a retry that
  the device never saw the answer to.
- `409 LANE_MODE_NOT_SET` when the lane has no direction. Events are kept and
  retried every 5 s. Fix it on the dashboard: **Lanes → gate-1 → ENTRY/EXIT**.
- `401` / `403` — bad key, inactive device, or wrong station. Events are kept
  and retried every 60 s. The warning is printed once, not per attempt.

The device never gives up on events. A 2000-event queue at 25 events per batch
is 80 batches, far more than a demo generates.

## Timestamps

The clock is left unset until NTP answers, and events carry **no** `timestamp`
field until then. The backend buckets movements by its own `received_at`, so a
device with no clock still produces correct movements. A wrong device clock
would be worse than none: it would look authoritative in the audit trail.

`PHYSICAL_PASSAGE` timestamps are the zone-entry time, per the project
specification, even though the event is only emitted when the zone clears.

## Tuning

`PASSAGE_ENTER_CM` / `PASSAGE_EXIT_CM` are the only values you should normally
need to touch. Measure the resting distance from the sensor to the far side of
the lane with nothing present, then set the exit threshold comfortably below
it. Enter must stay at least a few centimetres below exit; that gap is the
hysteresis band that stops a reading hovering at the threshold from producing
many short passages.

| Symptom | Change |
| --- | --- |
| One vehicle counted as several | Raise `CLEAR_CONFIRM_SAMPLES` (4 → 6), widen the gap between enter and exit |
| Passages missed | Lower `PASSAGE_ENTER_CM`, lower `OCCUPIED_CONFIRM_SAMPLES` |
| Phantom passages with an empty lane | Raise `PASSAGE_ENTER_CM` away from the resting distance, raise `MIN_OCCUPIED_MS` |
| Same tag reported repeatedly | Raise `RFID_COOLDOWN_MS` |

At `LOOP_MS = 20`, three occupied samples is about 60 ms and four clear
samples is about 80 ms. Lowering `LOOP_MS` tightens those windows; raising it
loosens them.

## Troubleshooting

| Log line | Cause | Fix |
| --- | --- | --- |
| `config.py not found` | `config.py` not on the device | copy `config.example.py` to `config.py`, edit, upload |
| `wifi: connect failed` | wrong SSID/password, or 2.4 GHz-only network | ESP32 cannot see 5 GHz networks |
| `clock: sync failed` | no route to an NTP server | harmless; movements use server time |
| `ingest: transport error 0` | laptop unreachable, wrong `API_BASE`, firewall | ping the laptop from the network, check the port |
| `400` and `ALLOWED_HOSTS` in the server log | laptop IP not allowed | add it to `DJANGO_ALLOWED_HOSTS` |
| `401` `DEVICE_AUTH_FAILED` | key mismatch, device inactive, wrong station | rotate key, check the Device row |
| `409` `LANE_MODE_NOT_SET` | lane has no direction | set ENTRY or EXIT on the dashboard |
| `rfid:` never appears | RC522 wiring, 3.3 V, or SPI pins | check 3.3 V, re-seat SDA/SCK/MOSI/MISO |
| `passage:` never appears | TRIG/ECHO swapped, 5 V missing | HC-SR04 needs 5 V on VCC |

Queue files live in `/queue` on the device. If the filesystem fills up, delete
them from the REPL:

```python
import os
for name in os.listdir('/queue'):
    os.remove('/queue/' + name)
```

Confirm what the backend received before deleting anything: a drained queue
plus a visible movement is success.

## Tests

The rules that are expensive to debug on hardware are in `logic.py` and run
under CPython:

```bash
cd firmware
../env/bin/python -m unittest discover -s tests -v
```

29 tests cover hysteresis, confirmation counts, missing-echo handling, per-UID
cooldown, event ids, tick wraparound, backoff and queue durability across a
reboot.

## Limits

- Reads 4-byte UIDs only. A 7-byte NTAG needs cascade level 2, which
  `mfrc522.py` does not implement. The backend accepts 4–32 hex characters, so
  the server is not the constraint.
- One tag at a time in the field. The RC522 anticollision loop is not
  implemented; two tags presented together may be reported as one, or as
  neither.
- No TLS certificate pinning. `https://` works if the firmware build has `ssl`,
  but the device is expected to run on a trusted LAN over plain HTTP for the
  demo.
- No local clock persistence across a power cycle. After every boot the clock is
  unset until NTP answers.
- The ultrasonic sensor measures distance to the nearest surface in its cone. A
  person standing beside the lane inside the cone is indistinguishable from a
  vehicle.
