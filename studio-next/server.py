#!/usr/bin/env python3
"""Independent YuE2 listening prototype. Original outputs are strictly read-only."""
import argparse
import datetime
import hashlib
import json
import mimetypes
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from render_bridge import build_render_call
from render_progress import enrich

ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def scan_outputs(source):
    tracks, plans, warnings = [], 0, []
    if not source.is_dir():
        return tracks, plans, ["Original outputs unavailable. Copied demo tracks remain playable."]
    for folder in source.iterdir():
        if not folder.is_dir() or folder.name in {"favorites_page", "FAVS"}:
            continue
        try:
            audio = sorted(p for p in folder.iterdir() if p.is_file() and
                           p.suffix.lower() in {".mp3", ".flac", ".wav", ".ogg", ".m4a"})
            audio.sort(key=lambda p: (p.suffix.lower() != ".mp3", p.name == "audio.flac", p.name))
            if not audio:
                plans += 1
                continue
            meta, req, result = [read_json(folder / n) for n in ("track.json", "request.json", "result.json")]
            if not all(isinstance(x, dict) for x in (meta, req, result)):
                warnings.append(f"Skipped invalid metadata in {folder.name}")
                continue
            rating = meta.get("rating")
            if rating is None and meta.get("favorite"):
                rating = 4
            try:
                rating = max(0, min(5, int(rating))) if rating is not None else None
            except (TypeError, ValueError):
                rating = None
            title = meta.get("display_name") or meta.get("track_title") or folder.name
            title = re.sub(r"^\d{4}-\d{2}-\d{2}_\d{6}_", "", title)
            title = re.sub(r"_?\[[^\]]*\]$", "", title).replace("_", " ").strip()
            mtime = folder.stat().st_mtime
            tracks.append({
                "id": hashlib.sha256(folder.name.encode()).hexdigest()[:16],
                "title": title, "folder": folder.name, "rating": rating,
                "created": meta.get("created") or datetime.datetime.fromtimestamp(mtime).isoformat(),
                "duration": meta.get("audio_seconds") or result.get("audio_seconds") or 0,
                "style": meta.get("style") or req.get("style", ""),
                "lyrics": meta.get("lyrics") or req.get("lyrics", ""),
                "parameters": meta.get("parameters", {}),
                "sound": meta.get("sound") or meta.get("preset", ""),
                "song": meta.get("song", ""), "notes": meta.get("notes", ""),
                "score": (folder / "score.abc").read_text(errors="replace") if (folder / "score.abc").exists() else "",
                "source_path": str(folder), "audio_path": str(audio[0]), "mtime": mtime, "curated": False,
            })
        except (OSError, TypeError, ValueError) as exc:
            warnings.append(f"Could not read {folder.name}: {exc}")
    return tracks, plans, warnings


class Catalog:
    def __init__(self):
        self.lock = LOCK
        self.config = {"source": str(ROOT / "outputs"), "original_studio": "http://127.0.0.1:7860",
                       **read_json(ROOT / "data/config.json")}
        self.starter = read_json(ROOT / "data/starter.json", [])
        self.items = {}
        self.last_scan = 0
        self.plans = 0
        self.warnings = []

    def refresh(self, force=False):
        with LOCK:
            if not force and self.last_scan and time.monotonic() - self.last_scan < 10:
                return
            tracks, self.plans, self.warnings = scan_outputs(Path(self.config["source"]))
            generated, generated_plans, _ = scan_outputs(ROOT / "outputs")
            self.plans += generated_plans
            if not tracks and self.warnings:
                # Catalog remains browsable if the original folder is disconnected.
                tracks = self.starter
            self.items = {t["id"]: t for t in tracks}
            for track in generated:
                self.items.setdefault(track["id"], track)
            for track in self.starter:
                # Keep copied audio available while reflecting current source ratings and text.
                current = self.items.get(track["id"], track)
                self.items[track["id"]] = dict(current, curated=True,
                                             **({"copied_audio": track["copied_audio"]} if track.get("copied_audio") else {}))
            self.last_scan = time.monotonic()

    def payload(self, force=False):
        self.refresh(force)
        with LOCK:
            state = read_json(ROOT / "data/state.json", {"overrides": {}, "playlists": [], "drafts": []})
            tracks = []
            for track in self.items.values():
                public = {k: v for k, v in track.items() if k not in {"audio_path", "copied_audio", "source_path"}}
                public.update(state.get("overrides", {}).get(track["id"], {}))
                public["audio"] = "/audio/" + track["id"]
                tracks.append(public)
            return {"tracks": tracks, "plans": self.plans, "warnings": self.warnings,
                    "state": state, "config": {k: v for k, v in self.config.items() if k not in {"source", "renderer_python"}},
                    "scanned_at": datetime.datetime.now().isoformat()}


CATALOG = Catalog()


def current_render():
    current = read_json(ROOT / "data/current-job.json")
    job_id = current.get("id", "")
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return {"state": "idle"}
    directory = ROOT / "data/jobs" / job_id
    state = read_json(directory / "status.json")
    if not directory.is_dir():
        return {"state": "idle"}
    with LOCK:
        return {"id": job_id, **enrich(directory, state)}


def start_render(composition):
    with LOCK:
        if current_render().get("state") in {"connecting", "rendering"}:
            raise ValueError("A track is already being created")
        schema = read_json(ROOT / "data/render-api.json", read_json(ROOT / "api-schema.json"))
        build_render_call(composition, schema)
        config = read_json(ROOT / "data/config.json")
        python = config.get("renderer_python", "")
        if not Path(python).is_file():
            raise ValueError("The existing YuE2 Python environment is unavailable")
        job_id = uuid.uuid4().hex
        directory = ROOT / "data/jobs" / job_id
        directory.mkdir(parents=True)
        (directory / "request.json").write_text(json.dumps(composition, indent=2, ensure_ascii=False))
        (directory / "status.json").write_text(json.dumps({"state": "connecting", "message": "Connecting to your YuE2 studio…", "title": composition.get("title", "Untitled composition")}))
        (ROOT / "data/current-job.json").write_text(json.dumps({"id": job_id}))
        try:
            with (directory / "render.log").open("wb") as log:
                subprocess.Popen([python, str(ROOT / "render_worker.py"), str(directory)],
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            (directory / "status.json").write_text(json.dumps({"state": "error", "message": str(exc)}))
            raise ValueError("Could not start the renderer bridge") from exc
        return current_render()


class Handler(BaseHTTPRequestHandler):
    def headers_for(self, status, mime, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def json_response(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.headers_for(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def file_response(self, path, audio=False, head=False):
        try:
            size = path.stat().st_size
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start, end, status = 0, size - 1, 200
            extra = {"Accept-Ranges": "bytes"} if audio else {}
            requested = self.headers.get("Range") if audio else None
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not match or not any(match.groups()):
                    self.headers_for(416, mime, 0, {"Content-Range": f"bytes */{size}"})
                    return
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), end) if right else end
                else:
                    start = max(0, size - int(right))
                if start >= size or start > end:
                    self.headers_for(416, mime, 0, {"Content-Range": f"bytes */{size}"})
                    return
                status = 206
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self.headers_for(status, mime, end - start + 1, extra)
            if not head:
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        block = stream.read(min(65536, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FileNotFoundError:
            self.json_response({"error": "File unavailable"}, 404)

    def route_get(self, head=False):
        parsed = urlparse(self.path)
        if parsed.path == "/api/library":
            self.json_response(CATALOG.payload(force=parsed.query == "refresh=1"))
        elif parsed.path == "/api/render":
            self.json_response(current_render())
        elif parsed.path.startswith("/listening/"):
            relative = parsed.path.removeprefix("/listening/") or "index.html"
            if relative not in {"index.html", "listening.css", "listening.js", "playlist.json", "playlist.js"} and not re.fullmatch(r"audio/[a-f0-9]{16}\.(mp3|flac|wav|ogg|m4a)", relative):
                self.json_response({"error": "Not found"}, 404)
                return
            self.file_response(ROOT / "glia-page" / relative, audio=relative.startswith("audio/"), head=head)
        elif parsed.path.startswith("/audio/"):
            CATALOG.refresh()
            item = CATALOG.items.get(parsed.path.removeprefix("/audio/"))
            if not item:
                self.json_response({"error": "Unknown track"}, 404)
                return
            path = ROOT / item["copied_audio"] if item.get("copied_audio") else Path(item["audio_path"])
            self.file_response(path, audio=True, head=head)
        else:
            allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/abcjs-basic-min.js": "abcjs-basic-min.js"}
            if parsed.path not in allowed:
                self.json_response({"error": "Not found"}, 404)
                return
            self.file_response(ROOT / "web" / allowed[parsed.path], head=head)

    def do_GET(self):
        self.route_get()

    def do_HEAD(self):
        self.route_get(head=True)

    def do_POST(self):
        # Browser writes must be same-origin JSON. Create delegates to the existing renderer.
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.json_response({"error": "Cross-origin writes rejected"}, 403)
            return
        if self.path not in {"/api/state", "/api/render"} or self.headers.get("Content-Type") != "application/json":
            self.json_response({"error": "Unsupported request"}, 400)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 2_000_000:
                raise ValueError("Invalid request size")
            state = json.loads(self.rfile.read(length))
            if self.path == "/api/render":
                self.json_response(start_render(state), 202)
                return
            if not isinstance(state, dict) or not isinstance(state.get("overrides"), dict) or not all(isinstance(state.get(k), list) for k in ("playlists", "drafts")):
                raise ValueError("Invalid state")
            for override in state["overrides"].values():
                if not isinstance(override, dict) or set(override) - {"rating", "notes", "title"}:
                    raise ValueError("Invalid track edit")
                rating = override.get("rating")
                if rating is not None and (type(rating) is not int or not 0 <= rating <= 5):
                    raise ValueError("Invalid rating")
                if "notes" in override and not isinstance(override["notes"], str):
                    raise ValueError("Invalid notes")
                if "title" in override and (not isinstance(override["title"], str) or not override["title"].strip() or len(override["title"]) > 160):
                    raise ValueError("Invalid title")
            with LOCK:
                (ROOT / "data").mkdir(exist_ok=True)
                temp = ROOT / "data/state.tmp"
                temp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
                temp.replace(ROOT / "data/state.json")
            self.json_response({"saved": True})
        except (ValueError, TypeError) as exc:
            self.json_response({"error": str(exc)}, 400)


if __name__ == "__main__":
    from publish_gallery import start_watcher
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7861)
    args = parser.parse_args()
    start_watcher(CATALOG, ROOT, ROOT / "glia-page")
    print(f"YuE2 Studio Next: http://127.0.0.1:{args.port} (original studio unchanged)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
