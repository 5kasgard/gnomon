"""Error correction: random damage within the code's limit is repaired
byte for byte; damage beyond it is reported, never silently "fixed"."""

from __future__ import annotations

import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from gnomon import Memory  # noqa: E402
from gnomon.ingest import record_messages  # noqa: E402
from gnomon.parity import BLOCK, DATA, PARITY, _C, _GEN, _invert, _matmul, check, protect, repair  # noqa: E402


class Field(unittest.TestCase):
    def test_every_choice_of_survivors_decodes(self):
        rng = np.random.default_rng(1)
        blocks = rng.integers(0, 256, size=(DATA, 64), dtype=np.uint8)
        code = np.vstack([blocks, _matmul(_C, blocks)])
        for _ in range(200):
            lost = set(rng.choice(DATA + PARITY, size=PARITY, replace=False).tolist())
            good = [i for i in range(DATA + PARITY) if i not in lost][:DATA]
            out = _matmul(_invert(_GEN[good]), code[good])
            self.assertTrue(np.array_equal(out, blocks))


class Repair(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "log.jsonl"
        m = Memory(self.log)
        m.register("owner", "owner", party="owner", kind="human", capabilities=["owner"],
                   compartments=["chat"], record_time="2024-01-01T00:00:00Z")
        rng = random.Random(3)
        for n in range(160):
            words = " ".join(rng.choice(["alpha", "beta", "gamma", "delta", "budget", "deadline"]) for _ in range(300))
            record_messages(m, "owner", [("user", f"Message {n}: {words}"), ("assistant", "Noted.")],
                            compartment="chat", record_time=f"2024-01-{2 + n // 24:02d}T{n % 24:02d}:00:00Z")
        self.m = m
        self.original = self.log.read_bytes()
        protect(self.log)

    def tearDown(self):
        self.dir.cleanup()

    def corrupt(self, offsets):
        b = bytearray(self.log.read_bytes())
        for o in offsets:
            b[o] ^= 0xFF
        self.log.write_bytes(bytes(b))

    def test_clean_log_checks_clean(self):
        self.assertEqual(check(self.log)["damaged"], [])
        self.assertGreater(len(self.original), 3 * BLOCK * DATA)  # several stripes

    def test_two_blocks_per_stripe_are_rebuilt_exactly(self):
        rng = random.Random(9)
        per = BLOCK * DATA
        offsets = []
        for s in range(len(self.original) // per):
            for blk in rng.sample(range(DATA), PARITY):
                offsets += [s * per + blk * BLOCK + rng.randrange(BLOCK) for _ in range(5)]
        self.corrupt(offsets)
        try:  # the hash chain sees the damage but cannot undo it
            seen = not Memory(self.log).verify().get("ok")
        except Exception:
            seen = True
        self.assertTrue(seen)
        rep = check(self.log)
        self.assertTrue(rep["damaged"] and rep["repairable"])
        repair(self.log)
        self.assertEqual(self.log.read_bytes(), self.original)
        self.assertTrue(Memory(self.log).verify()["ok"])

    def test_a_wiped_sector_is_rebuilt(self):
        b = bytearray(self.log.read_bytes())
        b[BLOCK * 3:BLOCK * 4] = bytes(BLOCK)
        self.log.write_bytes(bytes(b))
        repair(self.log)
        self.assertEqual(self.log.read_bytes(), self.original)

    def test_too_much_damage_is_refused(self):
        self.corrupt([BLOCK * i + 7 for i in range(PARITY + 1)])
        rep = check(self.log)
        self.assertFalse(rep["repairable"])
        with self.assertRaises(ValueError):
            repair(self.log)

    def test_growth_is_reported_then_protected_incrementally(self):
        record_messages(self.m, "owner", [("user", "Never use bullet points.")], compartment="chat",
                        record_time="2024-02-01T00:00:00Z")
        self.assertGreater(check(self.log)["unprotected_bytes"], 0)
        protect(self.log)
        self.assertEqual(check(self.log)["unprotected_bytes"], 0)
        self.corrupt([len(self.original) + 3])
        repair(self.log)
        self.assertTrue(Memory(self.log).verify()["ok"])

    def test_damaged_parity_blocks_are_found_and_recomputed(self):
        side = Path(str(self.log) + ".parity")
        raw = bytearray(side.read_bytes())
        raw[10] ^= 0x01
        side.write_bytes(bytes(raw))
        rep = check(self.log)
        self.assertEqual(rep["damaged"][0]["blocks"], [DATA])
        repair(self.log)
        self.assertEqual(self.log.read_bytes(), self.original)
        self.assertEqual(check(self.log)["damaged"], [])

    def test_a_damaged_parity_index_is_detected(self):
        side = Path(str(self.log) + ".parity.json")
        raw = bytearray(side.read_bytes())
        raw[len(raw) // 2] ^= 0x01
        side.write_bytes(bytes(raw))
        with self.assertRaises(ValueError):
            check(self.log)


if __name__ == "__main__":
    unittest.main()
