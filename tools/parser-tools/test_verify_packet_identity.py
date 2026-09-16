"""Regression cases for full-NAL identity, requiring no captured credentials."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from verify_packet_identity import annex_b_nals, main, nal_identity


class PacketIdentityTests(unittest.TestCase):
    def setUp(self):
        self.nal = b"\x65" + bytes(range(1, 256)) * 3

    def test_complete_nal_inside_packet(self):
        self.assertEqual(nal_identity(self.nal, [b"prefix" + self.nal + b"suffix"]),
                         "identical")

    def test_middle_needle_does_not_prove_identity(self):
        damaged = bytearray(self.nal)
        damaged[10] ^= 1
        self.assertEqual(nal_identity(self.nal, [bytes(damaged)]), "needle-only")

    def test_later_candidate_can_be_exact(self):
        damaged = bytearray(self.nal)
        damaged[-10] ^= 1
        self.assertEqual(nal_identity(self.nal, [bytes(damaged), self.nal]), "identical")

    def test_do_not_join_unrelated_packets(self):
        self.assertEqual(nal_identity(self.nal, [self.nal[:200], self.nal[200:]]),
                         "needle-only")

    def test_missing_nal(self):
        self.assertEqual(nal_identity(self.nal, [b"\xff" * 1000]), "absent")

    def test_three_and_four_byte_start_codes(self):
        stream = b"\x00\x00\x00\x01" + self.nal + b"\x00\x00\x01\x09\xf0"
        stream += b"\x00\x00\x00\x01\x67\x42\x80"
        parsed = annex_b_nals(stream)
        self.assertEqual([stream[o:o + n] for _, o, n in parsed],
                         [self.nal, b"\x09\xf0", b"\x67\x42\x80"])
        self.assertEqual([t for t, _, _ in parsed], [5, 9, 7])

    def test_report_labels_partial_matches_and_skips_small_nals(self):
        with tempfile.TemporaryDirectory() as pairing:
            with open(os.path.join(pairing, "test.h264"), "wb") as out:
                out.write(b"\x00\x00\x01" + self.nal + b"\x00\x00\x01\x09\xf0")
            damaged = bytearray(self.nal)
            damaged[10] ^= 1
            with open(os.path.join(pairing, "test.bin"), "wb") as out:
                out.write(damaged)
            output = io.StringIO()
            with patch.object(sys, "argv", ["verify_packet_identity.py", "--pairing", pairing]):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(), 0)
            self.assertIn("skipped(<256)=1", output.getvalue())
            self.assertIn("backward checked 1 NAL(s) >=256 B -> identical 0, needle-only 1, absent 0",
                          output.getvalue())


if __name__ == "__main__":
    unittest.main()
