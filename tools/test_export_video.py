import unittest
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from export_video import fetch_full, parse_vod, publish, request_fields, select_playlist

FIRST = "119354-aaaaaaaa-0000-4000-8000-000000000001.ts"
LAST = "119354-bbbbbbbb-0000-4000-8000-000000000002.ts"
TEXT = f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:8.333333,\n{FIRST}\n#EXTINF:2.500000,\n{LAST}\n#EXT-X-ENDLIST\n"


class CompletePlaylistTests(unittest.TestCase):
    def test_preserves_original_order_and_last_segment_duration(self):
        vod = parse_vod(TEXT)
        self.assertEqual(vod["names"], [FIRST, LAST])
        self.assertAlmostEqual(vod["seconds"], 10.833333)

    def test_missing_end_marker_is_not_a_complete_lesson(self):
        with self.assertRaises(ValueError):
            parse_vod(TEXT.replace("#EXT-X-ENDLIST", ""))

    def test_duplicate_or_late_start_is_refused(self):
        for text in [TEXT.replace(LAST, FIRST), TEXT.replace("MEDIA-SEQUENCE:0", "MEDIA-SEQUENCE:5")]:
            with self.assertRaises(ValueError):
                parse_vod(text)

    def test_missing_duration_and_invalid_names_are_refused(self):
        for text in [TEXT.replace("#EXTINF:2.500000,\n", ""), TEXT.replace(LAST, "video.mp4")]:
            with self.assertRaises(ValueError):
                parse_vod(text)

    def test_playback_window_selects_its_whole_playlist_not_other_lesson(self):
        vod = parse_vod(TEXT)
        other = parse_vod(TEXT.replace("119354-", "999999-"))
        key, names = request_fields(f"app_version=5.0.5&evs_playkey=session-key&ts_liststr=0|0|{LAST}&type=0")
        self.assertEqual(key, "session-key")
        self.assertIs(select_playlist([other, vod, vod], names), vod)
        with self.assertRaises(ValueError):
            select_playlist([other], names)

    def test_two_different_matching_playlists_are_ambiguous(self):
        vod = parse_vod(TEXT)
        shorter = parse_vod(TEXT.replace(f"#EXTINF:2.500000,\n{LAST}\n", ""))
        with self.assertRaises(ValueError):
            select_playlist([vod, shorter], [FIRST])

    def test_long_video_batches_preserve_global_order_and_final_short_batch(self):
        names = [f"119354-aaaaaaaa-0000-4000-8000-{i:012x}.ts" for i in range(205)]
        def server(command, stage, log):
            source = Path(command[command.index("--from-capture") + 1])
            _, requested = request_fields(json.loads(source.read_text())["md5_input"])
            payload = {"d_p": "http://example.test/lesson", "k_l": [
                {"idx": i, "sf": f"/{name}?sign=fake", "tk": "0" * 32}
                for i, name in reversed(list(enumerate(requested)))]}
            Path(command[command.index("--output") + 1]).write_text(json.dumps(payload))
        with tempfile.TemporaryDirectory() as folder, patch("export_video.run", side_effect=server) as calls:
            work = Path(folder)
            result = fetch_full({"names": names}, "fake-playkey", "fake-token", SimpleNamespace(cli="evmedia"), work, work / "log")
            self.assertEqual(calls.call_count, 3)
            self.assertEqual([e["idx"] for e in result["k_l"]], list(range(205)))
            self.assertEqual([e["sf"].split("?")[0][1:] for e in result["k_l"]], names)

    def test_partial_server_reply_never_becomes_a_full_playlist(self):
        def server(command, stage, log):
            Path(command[command.index("--output") + 1]).write_text(json.dumps({"d_p": "http://example.test", "k_l": []}))
        with tempfile.TemporaryDirectory() as folder, patch("export_video.run", side_effect=server):
            work = Path(folder)
            with self.assertRaises(RuntimeError):
                fetch_full({"names": [FIRST]}, "fake-key", "fake-token", SimpleNamespace(cli="evmedia"), work, work / "log")
            self.assertFalse((work / "list.json").exists())

    def test_windows_preview_lock_retries_publication_without_redecoding(self):
        busy = OSError("preview is reading the file")
        busy.winerror = 32
        with patch.object(Path, "replace", side_effect=[busy, None]) as replace, patch("export_video.time.sleep") as delay:
            publish(Path("source.partial.mp4"), Path("output.mp4"))
            self.assertEqual(replace.call_count, 2)
            delay.assert_called_once_with(.25)


if __name__ == "__main__":
    unittest.main()
