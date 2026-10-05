import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_bridge
import render_worker

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "api-schema.json").read_text())


class TestRenderBridge(unittest.TestCase):
    def composition(self, score=""):
        return {"title": "My new title", "style": "My own production prompt", "lyrics": "My own lyrics",
                "score": score, "parameters": {"seed": 777, "flow_steps": 24, "sem_temp": 1.3}}

    def test_without_score_plans_a_new_song(self):
        endpoint, args = render_bridge.build_render_call(self.composition(), SCHEMA)
        self.assertEqual(endpoint, "/one_click_generate_step")
        self.assertEqual(args["custom_title"], "My new title")
        self.assertEqual(args["flow_steps"], 24)
        self.assertEqual(args["lyrics"], "My own lyrics")
        self.assertIsNone(args["song_name"])

    def test_existing_score_and_settings_do_not_replace_text(self):
        endpoint, args = render_bridge.build_render_call(self.composition("X:1\nK:C\nCDEF|"), SCHEMA)
        self.assertEqual(endpoint, "/synthesize_audio_step")
        self.assertEqual(args["abc_text"], "X:1\nK:C\nCDEF|")
        self.assertEqual(args["track_title"], "My new title")
        self.assertEqual(args["style"], "My own production prompt")
        self.assertEqual(args["ode_steps"], 24)
        self.assertEqual(args["seed"], 777)

    def test_prompt_and_parameter_validation(self):
        draft = self.composition()
        draft["style"] = ""
        with self.assertRaises(ValueError):
            render_bridge.build_render_call(draft, SCHEMA)
        draft = self.composition()
        draft["parameters"]["sem_temp"] = float("nan")
        with self.assertRaises(ValueError):
            render_bridge.build_render_call(draft, SCHEMA)

    def test_completed_job_copies_audio_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "data/config.json").write_text(json.dumps({"original_studio": "http://127.0.0.1:7860"}))
            (root / "data/render-api.json").write_text(json.dumps(SCHEMA))
            job_dir = root / "data/jobs/test"
            job_dir.mkdir(parents=True)
            (job_dir / "request.json").write_text(json.dumps(self.composition()))
            original = root / "original"
            original.mkdir()
            audio = original / "newly-created.mp3"
            audio.write_bytes(b"fake audio for isolation test")
            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass

                def submit(self, **kwargs):
                    return SimpleNamespace(done=lambda: True, result=lambda: ({"path": str(audio)}, "X:1\nK:C\nCDEF|", "metrics", "done"))
            with patch.object(render_worker, "ROOT", root), patch.dict(sys.modules, {"gradio_client": SimpleNamespace(Client=FakeClient)}):
                render_worker.run(job_dir)
            self.assertEqual(audio.read_bytes(), b"fake audio for isolation test")
            copy = root / "outputs/newly-created/newly-created.mp3"
            self.assertEqual(copy.read_bytes(), audio.read_bytes())
            self.assertEqual(json.loads((job_dir / "status.json").read_text())["state"], "complete")
            metadata = json.loads((copy.parent / "track.json").read_text())
            self.assertEqual(metadata["lyrics"], "My own lyrics")


if __name__ == "__main__":
    unittest.main()
