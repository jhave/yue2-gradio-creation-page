#!/usr/bin/env python3
"""
Rebuild track.json for renders made before the sidecar existed.

Every folder already carries config.json (the full effective sampling config),
request.json (style, lyrics, seed, cot) and result.json (duration, timing). That
is everything track.json holds, so the parameters lost when they stopped being
encoded in filenames can be recovered exactly.

Existing track.json fields — favorite, notes — are preserved.

    python3 backfill_metadata.py                 # report what would be written
    python3 backfill_metadata.py --apply         # write them
    python3 backfill_metadata.py --star-from FAVS --apply
        also stars each source track whose audio was copied into outputs/FAVS,
        matching by filename stem and verifying by byte size
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

OUTPUTS = Path(__file__).resolve().parent / "outputs"

# track.json key <- (config.json section, key)
SAMPLING_MAP = [
    ("score_temp", "abc", "temperature"),
    ("score_top_p", "abc", "top_p"),
    ("score_rep_pen", "abc", "repetition_penalty"),
    ("score_top_k", "abc", "top_k"),
    ("score_pen_win", "abc", "penalty_window"),
    ("sem_temp", "semantic", "temperature"),
    ("sem_top_p", "semantic", "top_p"),
    ("rep_pen", "semantic", "repetition_penalty"),
    ("sem_top_k", "semantic", "top_k"),
    ("sem_pen_win", "semantic", "penalty_window"),
    ("sem_min_tokens", "semantic", "min_tokens"),
    ("sem_max_tokens", "semantic", "max_tokens"),
]


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_metadata(folder):
    config = read_json(folder / "config.json")
    request = read_json(folder / "request.json")
    result = read_json(folder / "result.json")
    if not config and not request:
        return None

    generation = config.get("generation", {})
    params = {}
    for key, section, field in SAMPLING_MAP:
        value = generation.get(section, {}).get(field)
        if value is not None:
            params[key] = value
    if "ode_steps" in generation:
        params["flow_steps"] = generation["ode_steps"]
    if config.get("cfg_scale") is not None:
        params["cfg_scale"] = config["cfg_scale"]
    if request.get("seed") is not None:
        params["seed"] = request["seed"]
    params["cot_mode"] = config.get("cot") or request.get("cot") or "full"

    audio = next(iter(sorted(folder.glob("*.flac"))), None) or next(iter(sorted(folder.glob("*.mp3"))), None)
    created = datetime.datetime.fromtimestamp(
        (audio or folder).stat().st_mtime
    ).isoformat(timespec="seconds")

    meta = {
        "folder": folder.name,
        "track_title": folder.name,
        "preset": "",
        "created": created,
        "favorite": False,
        "style": request.get("style", ""),
        "lyrics": request.get("lyrics", ""),
        "parameters": params,
        "backfilled_from": "config.json + request.json",
    }
    if result.get("audio_seconds"):
        meta["audio_seconds"] = round(float(result["audio_seconds"]), 1)
    e2e = (result.get("timing") or {}).get("e2e_seconds")
    if e2e:
        meta["render_seconds"] = round(float(e2e), 1)
    return meta


def favorites_from(folder_name):
    """Map stem -> size for each audio file in the given folder."""
    source = OUTPUTS / folder_name
    if not source.is_dir():
        sys.exit(f"No folder at {source}")
    found = {}
    for f in sorted(source.iterdir()):
        if f.suffix.lower() in {".flac", ".mp3", ".wav"} and f.is_file():
            found[f.stem] = f.stat().st_size
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--star-from", metavar="FOLDER",
                    help="star tracks whose audio was copied into this outputs/ subfolder")
    args = ap.parse_args()

    stars = favorites_from(args.star_from) if args.star_from else {}
    matched, unmatched = set(), dict(stars)

    written = starred = 0
    for folder in sorted(p for p in OUTPUTS.iterdir() if p.is_dir()):
        if args.star_from and folder.name == args.star_from:
            continue
        if folder.name == "favorites_page":
            continue

        meta = build_metadata(folder)
        if meta is None:
            continue

        existing = read_json(folder / "track.json")
        for keep in ("favorite", "notes", "preset", "track_title"):
            if existing.get(keep):
                meta[keep] = existing[keep]

        star_note = ""
        if folder.name in stars:
            sizes = {f.stat().st_size for f in folder.glob("*.flac")}
            verified = stars[folder.name] in sizes if sizes else False
            meta["favorite"] = True
            starred += 1
            matched.add(folder.name)
            unmatched.pop(folder.name, None)
            star_note = "  ★" + ("" if verified else "  (size differs — check this one)")

        had = (folder / "track.json").exists()
        action = "update" if had else "create"
        print(f"{action:7} {folder.name[:52]:54}{len(meta['parameters']):2d} params{star_note}")
        if args.apply:
            (folder / "track.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        written += 1

    print(f"\n{written} track.json {'written' if args.apply else 'to write'}", end="")
    if args.star_from:
        print(f"; {starred} starred from {args.star_from}/")
        if unmatched:
            print(f"\nNo source folder matched {len(unmatched)} file(s) in {args.star_from}/:")
            for stem in unmatched:
                print(f"  {stem}")
            print("  Do NOT delete those — they exist only there.")
        else:
            print(f"Every file in {args.star_from}/ has a starred source folder; "
                  f"the copies are redundant.")
    else:
        print(".")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
