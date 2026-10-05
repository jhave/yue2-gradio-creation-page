#!/usr/bin/env python3
"""Connect this independent workspace to an existing local studio."""
import argparse
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", required=True, type=Path, help="Existing YuE2 outputs directory")
    parser.add_argument("--python", type=Path, help="Python in the existing YuE2 environment (with gradio_client)")
    parser.add_argument("--studio", default="http://127.0.0.1:7860")
    parser.add_argument("--overwrite", action="store_true", help="Replace this workspace's connection settings")
    args = parser.parse_args()
    source = args.outputs.expanduser().resolve()
    if not source.is_dir():
        parser.error("The outputs directory does not exist")
    python = args.python.expanduser().resolve() if args.python else None
    if python and not python.is_file():
        parser.error("The renderer Python executable does not exist")
    directory = ROOT / "data"
    directory.mkdir(exist_ok=True)
    config_path = directory / "config.json"
    if config_path.exists() and not args.overwrite:
        parser.error("Connection settings already exist; use --overwrite to replace them")
    config = {"source": str(source), "original_studio": args.studio.rstrip("/")}
    if python:
        config["renderer_python"] = str(python)
        # Capture the engine's API before saving connection settings. No render is submitted.
        with urlopen(config["original_studio"] + "/gradio_api/info", timeout=15) as response:
            endpoints = json.load(response)["named_endpoints"]
        schema = {name: endpoints[name] for name in ("/one_click_generate_step", "/synthesize_audio_step")}
        (directory / "render-api.json").write_text(json.dumps(schema, indent=2) + "\n")
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print("Connected. Start with: python3 server.py --port 7861")
    print("Original outputs are read for listening. Create explicitly asks the engine for a new render.")


if __name__ == "__main__":
    main()
