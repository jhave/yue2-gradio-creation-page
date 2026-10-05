"""Checks discovery, isolated edits, and seeking without touching real outputs."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "original"
        self.source.mkdir()
        self.workspace = self.root / "prototype"
        (self.workspace / "data").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def render(self, name, metadata=None, extension=".mp3"):
        path = self.source / name
        path.mkdir()
        (path / (name + extension)).write_bytes(b"0123456789")
        if metadata is not None:
            (path / "track.json").write_text(json.dumps(metadata))
        return path

    def handler(self, headers=None, body=b"", path="/api/state"):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = headers or {}
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.path = path
        handler.status, handler.response_headers = None, {}
        handler.send_response = lambda status: setattr(handler, "status", status)
        handler.send_header = lambda key, value: handler.response_headers.update({key: value})
        handler.end_headers = lambda: None
        return handler

    def test_new_render_appears_after_scan(self):
        self.render("first", {"rating": 4})
        first, _, _ = server.scan_outputs(self.source)
        self.render("newest", {"track_title": "Newly rendered", "created": "2026-10-05"})
        second, _, _ = server.scan_outputs(self.source)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        self.assertIsNone(next(t for t in second if t["title"] == "Newly rendered")["rating"])

    def test_plans_separate_and_legacy_star_preserved(self):
        self.render("legacy", {"favorite": True})
        (self.source / "plan-only").mkdir()
        (self.source / "favorites_page").mkdir()
        tracks, plans, warnings = server.scan_outputs(self.source)
        self.assertEqual(tracks[0]["rating"], 4)
        self.assertEqual(plans, 1)
        self.assertFalse(warnings)

    def test_wav_only_and_corrupt_json_remain_visible(self):
        folder = self.render("wav-only", extension=".wav")
        (folder / "track.json").write_text("{incomplete")
        tracks, _, _ = server.scan_outputs(self.source)
        self.assertEqual(len(tracks), 1)
        self.assertTrue(tracks[0]["audio_path"].endswith(".wav"))

    def test_catalog_retains_copied_tracks_if_source_missing(self):
        config = {"source": str(self.root / "missing")}
        starter = [{"id": "abc", "title": "Demo", "copied_audio": "media/abc/audio.mp3"}]
        (self.workspace / "data/config.json").write_text(json.dumps(config))
        (self.workspace / "data/starter.json").write_text(json.dumps(starter))
        with patch.object(server, "ROOT", self.workspace):
            payload = server.Catalog().payload()
        self.assertEqual(payload["tracks"][0]["title"], "Demo")
        self.assertTrue(payload["warnings"])

    def test_empty_checkout_opens_and_can_save_a_draft(self):
        empty = self.root / "fresh-checkout"
        empty.mkdir()
        with patch.object(server, "ROOT", empty):
            payload = server.Catalog().payload()
            self.assertEqual(payload["tracks"], [])
            state = {"overrides": {}, "playlists": [], "drafts": [{"title": "First idea"}]}
            body = json.dumps(state).encode()
            handler = self.handler({"Content-Type": "application/json", "Content-Length": str(len(body))}, body)
            handler.do_POST()
            self.assertEqual(handler.status, 200)
            self.assertEqual(json.loads((empty / "data/state.json").read_text()), state)

    def test_edits_write_only_to_new_workspace(self):
        original = self.render("track", {"rating": 5})
        before = (original / "track.json").read_bytes()
        state = {"overrides": {"id": {"rating": 3, "notes": "Try a softer outro"}}, "playlists": [], "drafts": []}
        body = json.dumps(state).encode()
        handler = self.handler({"Content-Type": "application/json", "Content-Length": str(len(body))}, body)
        with patch.object(server, "ROOT", self.workspace):
            handler.do_POST()
        self.assertEqual(handler.status, 200)
        self.assertEqual(json.loads((self.workspace / "data/state.json").read_text()), state)
        self.assertEqual((original / "track.json").read_bytes(), before)

    def test_cross_origin_and_invalid_edit_rejected(self):
        body = json.dumps({"overrides": {"id": {"audio_path": "/bad"}}, "playlists": [], "drafts": []}).encode()
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body)), "Origin": "https://other.example", "Host": "127.0.0.1:7861"}
        handler = self.handler(headers, body)
        handler.do_POST()
        self.assertEqual(handler.status, 403)
        headers.pop("Origin")
        handler = self.handler(headers, body)
        handler.do_POST()
        self.assertEqual(handler.status, 400)

    def test_audio_range_supports_seeking(self):
        folder = self.render("track")
        handler = self.handler({"Range": "bytes=3-6"})
        handler.file_response(folder / "track.mp3", audio=True)
        self.assertEqual(handler.status, 206)
        self.assertEqual(handler.wfile.getvalue(), b"3456")
        self.assertEqual(handler.response_headers["Content-Range"], "bytes 3-6/10")

    def test_invalid_audio_range_rejected(self):
        folder = self.render("track")
        handler = self.handler({"Range": "bytes=40-50"})
        handler.file_response(folder / "track.mp3", audio=True)
        self.assertEqual(handler.status, 416)


if __name__ == "__main__":
    unittest.main()
