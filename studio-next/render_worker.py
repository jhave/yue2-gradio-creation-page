#!/usr/bin/env python3
"""Render only on an explicit Create request, through the already running engine."""
import datetime
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from render_bridge import build_render_call

ROOT = Path(__file__).resolve().parent


def write_status(directory, **state):
    state["updated"] = datetime.datetime.now().isoformat()
    temp = directory / "status.tmp"
    temp.write_text(json.dumps(state, ensure_ascii=False))
    temp.replace(directory / "status.json")


def run(directory):
    composition = json.loads((directory / "request.json").read_text())
    config = json.loads((ROOT / "data/config.json").read_text())
    schema_path = ROOT / "data/render-api.json"
    if not schema_path.exists():
        schema_path = ROOT / "api-schema.json"
    schema = json.loads(schema_path.read_text())
    endpoint, arguments = build_render_call(composition, schema)
    from gradio_client import Client
    write_status(directory, state="connecting", message="Connecting to your YuE2 studio…", title=composition["title"])
    client = Client(config["original_studio"], verbose=False, analytics_enabled=False, download_files=False)
    job = client.submit(api_name=endpoint, **arguments)
    while not job.done():
        status = job.status()
        message = "Rendering with the existing studio…" if status.code.value == "PROCESSING" else "Waiting for the renderer…"
        progress = None
        if status.progress_data:
            update = status.progress_data[-1]
            message = update.desc or message
            progress = update.progress
        write_status(directory, state="rendering", message=message, progress=progress,
                     title=composition["title"], phase=status.code.value)
        time.sleep(2)
    result = job.result()
    if not isinstance(result, (tuple, list)) or not result or not result[0]:
        raise RuntimeError("The renderer returned no audio. " + str(result))
    file_data = result[0]
    audio_path = Path(file_data["path"] if isinstance(file_data, dict) else file_data)
    if not audio_path.is_file():
        raise RuntimeError("The rendered audio file could not be found on this computer")
    # Copy the new result into the prototype; never move or rewrite an original.
    folder_name = audio_path.stem
    output = ROOT / "outputs" / folder_name
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(audio_path, output / audio_path.name)
    original_meta = audio_path.parent / "track.json"
    try:
        metadata = json.loads(original_meta.read_text()) if original_meta.exists() else {}
    except ValueError:
        metadata = {}
    metadata.update(folder=folder_name, track_title=composition["title"], style=composition["style"],
                    lyrics=composition.get("lyrics", ""), parameters=composition["parameters"],
                    created=datetime.datetime.now().isoformat(), next_studio_job=directory.name)
    if not metadata.get("audio_seconds"):
        try:
            import soundfile
            metadata["audio_seconds"] = round(soundfile.info(str(audio_path)).duration, 1)
        except Exception:
            pass
    (output / "track.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    (output / "request.json").write_text(json.dumps(composition, indent=2, ensure_ascii=False))
    score = composition.get("score", "") if endpoint == "/synthesize_audio_step" else result[1]
    (output / "score.abc").write_text(score or "")
    track_id = hashlib.sha256(folder_name.encode()).hexdigest()[:16]
    write_status(directory, state="complete", message="Your track is ready", title=composition["title"], track_id=track_id)


if __name__ == "__main__":
    directory = Path(sys.argv[1]).resolve()
    try:
        run(directory)
    except Exception as exc:
        write_status(directory, state="error", message=str(exc))
        raise
