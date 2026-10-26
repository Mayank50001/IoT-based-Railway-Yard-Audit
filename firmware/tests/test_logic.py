"""Tests for the portable firmware logic.

These run under CPython, not on the ESP32:

    cd firmware
    ../env/bin/python -m unittest discover -s tests -v

The point is to lock down the rules that are expensive to debug on hardware:
hysteresis, confirmation counts, missing-echo handling, per-UID cooldown, event
ids, backoff, and queue durability.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from logic import (  # noqa: E402
    EventQueue,
    PassageDetector,
    UidGate,
    backoff_seconds,
    elapsed_ms,
    iso_utc,
    make_event_id,
    ticks_add,
    TICKS_MASK,
)

EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class PassageDetectorTests(unittest.TestCase):
    def detector(self, **overrides):
        options = {
            "enter_cm": 20.0,
            "exit_cm": 25.0,
            "occupied_samples": 3,
            "clear_samples": 3,
            "min_occupied_ms": 100,
        }
        options.update(overrides)
        return PassageDetector(**options)

    def confirm_occupied(self, detector, times=(100, 150, 200), distance=10):
        for moment in times:
            detector.observe(distance, moment)

    def test_confirmation_is_required_before_a_passage_starts(self):
        detector = self.detector()

        detector.observe(30, 0)
        self.assertFalse(detector.occupied)

        detector.observe(10, 100)
        detector.observe(10, 150)
        self.assertFalse(detector.occupied)

        detector.observe(10, 200)
        self.assertTrue(detector.occupied)
        # Entry time is the first sample, not the confirming one.
        self.assertEqual(detector.entry_ms, 100)

    def test_single_glitch_does_not_start_a_passage(self):
        detector = self.detector()
        detector.observe(10, 0)
        detector.observe(30, 50)
        detector.observe(30, 100)
        self.assertFalse(detector.occupied)
        self.assertEqual(detector.occupied_count, 0)

    def test_completed_passage_reports_duration_and_range(self):
        detector = self.detector()
        self.confirm_occupied(detector, distance=10)

        detector.observe(8, 250)
        detector.observe(30, 300)
        detector.observe(30, 350)
        passage = detector.observe(30, 400)

        self.assertIsNotNone(passage)
        self.assertEqual(passage["duration_ms"], 200)  # 300 - 100
        self.assertEqual(passage["min_cm"], 8)
        self.assertEqual(passage["max_cm"], 30)
        self.assertFalse(detector.occupied)

    def test_hysteresis_band_does_not_start_a_passage(self):
        detector = self.detector()
        for moment in range(0, 500, 50):
            detector.observe(22, moment)
        self.assertFalse(detector.occupied)

    def test_hysteresis_band_does_not_end_a_passage(self):
        detector = self.detector()
        self.confirm_occupied(detector, distance=10)
        for moment in range(250, 750, 50):
            detector.observe(22, moment)
        self.assertTrue(detector.occupied)

    def test_missing_echo_while_occupied_never_ends_the_passage(self):
        detector = self.detector()
        self.confirm_occupied(detector, distance=10)

        for moment in range(250, 1000, 50):
            detector.observe(None, moment)
        self.assertTrue(detector.occupied)

        detector.observe(30, 1000)
        detector.observe(30, 1050)
        passage = detector.observe(30, 1100)
        self.assertIsNotNone(passage)
        self.assertTrue(detector.occupied is False)

    def test_missing_echo_while_clear_does_not_start_a_passage(self):
        detector = self.detector()
        for moment in range(0, 500, 50):
            detector.observe(None, moment)
        self.assertFalse(detector.occupied)

    def test_short_blip_is_discarded(self):
        detector = self.detector(min_occupied_ms=120)
        self.confirm_occupied(detector, times=(0, 10, 20), distance=10)

        detector.observe(30, 30)
        detector.observe(30, 40)
        passage = detector.observe(30, 50)

        self.assertIsNone(passage)
        self.assertFalse(detector.occupied)

    def test_occupied_ms_tracks_elapsed_time(self):
        detector = self.detector()
        self.confirm_occupied(detector, times=(1000, 1050, 1100), distance=10)
        self.assertEqual(detector.occupied_ms(1600), 600)
        self.assertEqual(detector.occupied_ms(900), 0)

    def test_reset_clears_state(self):
        detector = self.detector()
        self.confirm_occupied(detector, distance=10)
        detector.reset()
        self.assertFalse(detector.occupied)
        self.assertIsNone(detector.entry_ms)

    def test_exit_threshold_must_be_above_enter_threshold(self):
        with self.assertRaises(ValueError):
            PassageDetector(20.0, 20.0)


class UidGateTests(unittest.TestCase):
    def test_first_read_emits_and_continuous_reads_do_not(self):
        gate = UidGate(cooldown_ms=3000)

        self.assertTrue(gate.observe("A", 0))
        self.assertFalse(gate.observe("A", 1000))
        self.assertFalse(gate.observe("A", 2500))
        self.assertFalse(gate.observe("A", 4000))

    def test_uid_emits_again_after_the_cooldown_without_reads(self):
        gate = UidGate(cooldown_ms=3000)
        self.assertTrue(gate.observe("A", 0))
        self.assertTrue(gate.observe("A", 3000))

    def test_different_uid_emits_immediately(self):
        gate = UidGate(cooldown_ms=3000)
        self.assertTrue(gate.observe("A", 0))
        self.assertTrue(gate.observe("B", 100))

    def test_returning_uid_within_cooldown_does_not_repeat(self):
        gate = UidGate(cooldown_ms=3000)
        self.assertTrue(gate.observe("A", 0))
        self.assertTrue(gate.observe("B", 100))
        self.assertFalse(gate.observe("A", 200))

    def test_tracking_is_bounded(self):
        gate = UidGate(cooldown_ms=1000, max_tracked=2)
        gate.observe("A", 0)
        gate.observe("B", 10)
        gate.observe("C", 20)

        self.assertLessEqual(len(gate.last_seen), 2)
        self.assertIn("C", gate.last_seen)


class TimeHelperTests(unittest.TestCase):
    def test_iso_utc_formats_epoch_in_utc(self):
        self.assertEqual(iso_utc(0), "1970-01-01T00:00:00.000Z")
        self.assertEqual(
            iso_utc(1700000000),
            "2023-11-14T22:13:20.000Z",
        )
        self.assertEqual(
            iso_utc(1700000000.5),
            "2023-11-14T22:13:20.500Z",
        )

    def test_iso_utc_rounds_milliseconds_across_a_second(self):
        self.assertEqual(
            iso_utc(1700000000.9996),
            "2023-11-14T22:13:21.000Z",
        )

    def test_event_id_is_stable_and_matches_the_server_pattern(self):
        event_id = make_event_id("gate-1", "7f3a2c", 42)
        self.assertEqual(event_id, "gate-1-7f3a2c-000042")
        self.assertRegex(event_id, EVENT_ID_PATTERN)
        self.assertLessEqual(len(event_id), 64)

    def test_elapsed_ms_handles_normal_and_wrapped_values(self):
        self.assertEqual(elapsed_ms(500, 100), 400)
        self.assertEqual(elapsed_ms(100, 500), -400)

        just_before_wrap = TICKS_MASK - 5
        self.assertEqual(elapsed_ms(10, just_before_wrap), 16)

    def test_ticks_add_wraps(self):
        self.assertEqual(ticks_add(TICKS_MASK - 5, 10), 4)

    def test_backoff_grows_then_caps(self):
        self.assertEqual(
            [backoff_seconds(value) for value in range(0, 8)],
            [1, 2, 4, 8, 16, 30, 30, 30],
        )


class EventQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="yard-queue-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def queue(self, boot_id="abc123", max_events=10):
        return EventQueue(
            self.directory,
            "gate-1",
            boot_id,
            max_events=max_events,
        )

    def test_append_assigns_sequence_and_event_id(self):
        queue = self.queue()

        first = queue.append({"event_type": "RFID_DETECTED"})
        second = queue.append({"event_type": "PHYSICAL_PASSAGE"})

        self.assertEqual(first["sequence"], 1)
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(first["event_id"], "gate-1-abc123-000001")
        self.assertEqual(queue.count(), 2)
        self.assertEqual(queue.last_seq, 2)

    def test_queue_survives_a_restart_with_a_new_boot_id(self):
        queue = self.queue(boot_id="abc123")
        queue.append({"event_type": "RFID_DETECTED"})
        queue.append({"event_type": "RFID_DETECTED"})

        restarted = self.queue(boot_id="def456")
        self.assertEqual(restarted.count(), 2)
        self.assertEqual(restarted.next_seq, 3)

        third = restarted.append({"event_type": "RFID_DETECTED"})
        self.assertEqual(third["sequence"], 3)
        self.assertEqual(third["event_id"], "gate-1-def456-000003")

        ids = [event["event_id"] for _, event in restarted.batch(10)]
        self.assertEqual(
            ids,
            [
                "gate-1-abc123-000001",
                "gate-1-abc123-000002",
                "gate-1-def456-000003",
            ],
        )

    def test_batch_returns_oldest_first_and_respects_limit(self):
        queue = self.queue()
        for _ in range(3):
            queue.append({"event_type": "RFID_DETECTED"})

        batch = queue.batch(2)
        self.assertEqual(len(batch), 2)
        self.assertEqual(
            [event["sequence"] for _, event in batch],
            [1, 2],
        )

    def test_drop_removes_files_and_entries(self):
        queue = self.queue()
        for _ in range(3):
            queue.append({"event_type": "RFID_DETECTED"})

        batch = queue.batch(2)
        queue.drop([path for path, _ in batch])

        self.assertEqual(queue.count(), 1)
        remaining = os.listdir(self.directory)
        self.assertEqual(remaining, ["00000003.json"])

    def test_overflow_drops_oldest_and_counts_it(self):
        queue = self.queue(max_events=2)
        for _ in range(3):
            queue.append({"event_type": "RFID_DETECTED"})

        self.assertEqual(queue.count(), 2)
        self.assertEqual(queue.dropped, 1)
        self.assertEqual(
            [event["sequence"] for _, event in queue.batch(10)],
            [2, 3],
        )

    def test_corrupt_and_temporary_files_are_discarded_at_load(self):
        with open(os.path.join(self.directory, "000009.json"), "w") as handle:
            handle.write("not json")
        with open(os.path.join(self.directory, "000010.json.tmp"), "w") as handle:
            handle.write("{}")
        with open(os.path.join(self.directory, "notes.txt"), "w") as handle:
            handle.write("ignored")

        queue = self.queue()

        self.assertEqual(queue.count(), 0)
        self.assertEqual(os.listdir(self.directory), ["notes.txt"])

    def test_stored_file_is_valid_json(self):
        queue = self.queue()
        record = queue.append({"event_type": "RFID_DETECTED", "rfid_uid": "FA37E63F"})

        path = os.path.join(self.directory, "00000001.json")
        with open(path) as handle:
            stored = json.load(handle)

        self.assertEqual(stored, record)


if __name__ == "__main__":
    unittest.main()
