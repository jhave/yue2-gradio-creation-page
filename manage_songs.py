#!/usr/bin/env python3
"""
YuE2 Track Manager CLI
Easily see, play, rename, and manage your generated tracks directly from the terminal.

Usage:
  ./manage_songs.py list                     # List all songs with duration, key, size, and date
  ./manage_songs.py play [track]             # Play a song via native macOS afplay (FLAC 48kHz)
  ./manage_songs.py play [track] -t 15       # Preview first 15 seconds
  ./manage_songs.py play [track] --system    # Play via macOS default app (QuickTime / Music)
  ./manage_songs.py rename <track> <new_name># Rename a track folder
  ./manage_songs.py info [track]             # Show musical score, timings, lyrics & prompt
  ./manage_songs.py finder [track]           # Open track directory in macOS Finder
  ./manage_songs.py clean                    # Remove empty test/scratch directories
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Auto-activate project virtual environment if run with system python
root_dir = Path(__file__).resolve().parent
venv_python = root_dir / ".venv" / "bin" / "python"
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

OUTPUTS_DIR = root_dir / "outputs"


def get_tracks():
    """Scan outputs directory and return sorted list of track metadata."""
    if not OUTPUTS_DIR.exists():
        return []

    tracks = []
    for d in sorted(OUTPUTS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue

        flac = d / "audio.flac"
        score_file = d / "score.abc"
        req_file = d / "request.json"

        # Calculate duration and key
        duration = "--"
        key_bpm = "--"
        score_text = ""

        if flac.exists():
            try:
                import soundfile as sf
                sec = sf.info(str(flac)).duration
                duration = f"{int(sec // 60)}m {int(sec % 60):02d}s"
            except Exception:
                pass

        if score_file.exists():
            try:
                score_text = score_file.read_text(encoding="utf-8", errors="ignore")
                unit = 1 / 8
                bpm = 120
                meter = (4, 4)
                key = ""
                for line in score_text.splitlines():
                    line = line.strip()
                    if line.startswith("L:"):
                        p = line[2:].strip().split("/")
                        if len(p) == 2:
                            unit = float(p[0]) / float(p[1])
                    elif line.startswith("Q:"):
                        m = re.search(r"Q:(?:1/4=)?(\d+)", line)
                        if m:
                            bpm = float(m.group(1))
                    elif line.startswith("M:"):
                        p = line[2:].strip().split("/")
                        if len(p) == 2:
                            meter = (int(p[0]), int(p[1]))
                    elif line.startswith("K:"):
                        key = line[2:].strip()

                key_bpm = f"{key} @ {int(bpm)} BPM" if key else f"{int(bpm)} BPM"
                if duration == "--":
                    bars = score_text.count("|")
                    if bars > 0 and bpm > 0:
                        beats_per_bar = meter[0] * (meter[1] / 4.0)
                        est_sec = bars * beats_per_bar / bpm * 60.0
                        mins = int(est_sec // 60)
                        secs = int(est_sec % 60)
                        duration = f"~{mins}m {secs:02d}s"
            except Exception:
                pass

        # Try getting style and lyrics from request.json
        style_prompt = ""
        lyrics_text = ""
        if req_file.exists():
            try:
                req = json.loads(req_file.read_text())
                style_prompt = req.get("style", "")
                lyrics_text = req.get("lyrics", "")
            except Exception:
                pass

        size_str = f"{flac.stat().st_size / (1024 * 1024):.1f} MB" if flac.exists() else "Plan Only"
        mtime = datetime.datetime.fromtimestamp(d.stat().st_mtime)

        tracks.append({
            "name": d.name,
            "has_audio": flac.exists(),
            "flac_path": flac if flac.exists() else None,
            "score_path": score_file if score_file.exists() else None,
            "score_text": score_text,
            "duration": duration,
            "key_bpm": key_bpm,
            "size": size_str,
            "mtime": mtime,
            "style": style_prompt,
            "lyrics": lyrics_text,
            "dir_path": d
        })

    # Sort descending by modification time
    tracks.sort(key=lambda t: t["mtime"], reverse=True)
    return tracks


def resolve_track(query, tracks):
    """Resolve a track name or 1-based index to a track dict."""
    if not tracks:
        return None

    if query is None or query == "":
        return tracks[0]  # default to most recent

    if query.isdigit():
        idx = int(query) - 1
        if 0 <= idx < len(tracks):
            return tracks[idx]

    for t in tracks:
        if t["name"].lower() == query.lower():
            return t
        if query.lower() in t["name"].lower():
            return t

    return None


def cmd_list(args):
    """List all songs in outputs/."""
    tracks = get_tracks()
    if not tracks:
        print(f"No songs found in {OUTPUTS_DIR}")
        return

    print("\n" + "=" * 90)
    print(f" YuE2 Audio Library ({len(tracks)} tracks in {OUTPUTS_DIR})")
    print("=" * 90)
    fmt = "{:<3} {:<32} {:<10} {:<18} {:<12} {:<12}"
    print(fmt.format("#", "Track Name", "Duration", "Key & Tempo", "Size", "Created"))
    print("-" * 90)

    for i, t in enumerate(tracks, 1):
        icon = "🎵" if t["has_audio"] else "📝"
        name_display = f"{icon} {t['name']}"
        date_str = t["mtime"].strftime("%b %d, %H:%M")
        print(fmt.format(i, name_display[:32], t["duration"], t["key_bpm"][:18], t["size"], date_str))

    print("-" * 90)
    print("💡 To play:    ./manage_songs.py play <#> or <name>")
    print("💡 To rename:  ./manage_songs.py rename <old_name> <new_name>")
    print("💡 In browser: http://127.0.0.1:7860 (Tab 2: Track Library)\n")


def cmd_play(args):
    """Play a song using afplay or system player."""
    tracks = get_tracks()
    target = resolve_track(args.track, tracks)

    if not target:
        print(f"❌ Track not found: '{args.track}'")
        cmd_list(args)
        return

    if not target["has_audio"]:
        print(f"❌ '{target['name']}' does not contain an audio.flac file (Plan only).")
        return

    flac = target["flac_path"]
    print(f"\n▶️  Playing: {target['name']}")
    print(f"   Duration: {target['duration']} | Key/BPM: {target['key_bpm']} | Size: {target['size']}")
    print(f"   Path: {flac}\n")

    if args.system:
        subprocess.run(["open", str(flac)])
        print("🔊 Opened in macOS default player (QuickTime / Music).")
        return

    cmd = ["afplay"]
    if args.time:
        cmd.extend(["-t", str(args.time)])
        print(f"⏱️  Previewing first {args.time} seconds...")
    if args.volume:
        cmd.extend(["-v", str(args.volume)])

    cmd.append(str(flac))

    try:
        print("🔊 Press Ctrl+C at any time to stop playback.\n")
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n⏹️  Playback stopped.")


def cmd_rename(args):
    """Rename a track folder."""
    tracks = get_tracks()
    target = resolve_track(args.old_name, tracks)

    if not target:
        print(f"❌ Track '{args.old_name}' not found.")
        return

    old_dir = target["dir_path"]
    clean_new_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", args.new_name.strip()).strip("_")
    if not clean_new_name:
        print("❌ Invalid new name. Please use alphanumeric characters, dashes, or underscores.")
        return

    new_dir = OUTPUTS_DIR / clean_new_name
    if new_dir.exists() and new_dir != old_dir:
        print(f"❌ A track named '{clean_new_name}' already exists.")
        return

    try:
        old_dir.rename(new_dir)
        print(f"✅ Successfully renamed '{target['name']}' -> '{clean_new_name}'")
    except Exception as e:
        print(f"❌ Error renaming: {e}")


def cmd_info(args):
    """Show detailed info for a track."""
    tracks = get_tracks()
    target = resolve_track(args.track, tracks)

    if not target:
        print(f"❌ Track not found: '{args.track}'")
        return

    print("\n" + "=" * 80)
    print(f" Track Details: {target['name']}")
    print("=" * 80)
    print(f"Folder:      {target['dir_path']}")
    print(f"Audio:       {'Present (audio.flac)' if target['has_audio'] else 'None'}")
    print(f"Duration:    {target['duration']}")
    print(f"Key & Tempo: {target['key_bpm']}")
    print(f"File Size:   {target['size']}")
    print(f"Created:     {target['mtime'].strftime('%Y-%m-%d %H:%M:%S')}")

    if target["style"]:
        print(f"\nStyle Prompt:\n{target['style']}")

    if target["lyrics"]:
        print(f"\nLyrics Excerpt:\n{target['lyrics'][:300]}...")

    if target["score_text"]:
        print("\nABC Score (First 20 lines):")
        lines = target["score_text"].splitlines()[:20]
        for l in lines:
            print("  " + l)
    print("=" * 80 + "\n")


def cmd_finder(args):
    """Open track directory or outputs/ in macOS Finder."""
    tracks = get_tracks()
    target = resolve_track(args.track, tracks) if args.track else None
    path = target["dir_path"] if target else OUTPUTS_DIR

    if path.exists():
        subprocess.run(["open", str(path)])
        print(f"📂 Opened in macOS Finder: {path}")
    else:
        print(f"❌ Path not found: {path}")


def cmd_clean(args):
    """Find and remove empty directories in outputs/."""
    tracks = get_tracks()
    cleaned = 0
    for d in OUTPUTS_DIR.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            files = list(d.iterdir())
            if len(files) == 0:
                print(f"🗑️  Removing empty directory: {d.name}")
                d.rmdir()
                cleaned += 1
    if cleaned == 0:
        print("✨ outputs/ is already clean (no empty folders).")
    else:
        print(f"✅ Cleaned {cleaned} empty folder(s).")


def cmd_presets(args):
    """List or inspect saved presets."""
    preset_file = root_dir / "presets.json"
    if not preset_file.exists():
        print("No presets.json found.")
        return

    data = json.loads(preset_file.read_text(encoding="utf-8"))
    if args.name:
        p = data.get(args.name)
        if not p:
            for k, v in data.items():
                if args.name.lower() in k.lower():
                    p = v
                    args.name = k
                    break
        if not p:
            print(f"❌ Preset '{args.name}' not found.")
            return

        print("\n" + "=" * 80)
        print(f" Preset: {args.name}")
        print("=" * 80)
        print(f"Title:          {p.get('custom_title', '--')}")
        print(f"Semantic Temp:  {p.get('sem_temp', '--')} (Top-P: {p.get('sem_top_p', '--')})")
        print(f"Score Temp:     {p.get('score_temp', '--')} (Top-P: {p.get('score_top_p', '--')})")
        print(f"Rep Penalty:    {p.get('rep_pen', '--')}")
        print(f"CFG Scale:      {p.get('cfg_scale', '--')}")
        print(f"Flow Steps:     {p.get('flow_steps', '--')}")
        print(f"Seed:           {p.get('seed', '--')}")
        print(f"\nStyle Prompt:\n{p.get('style', '')}")
        print(f"\nLyrics:\n{p.get('lyrics', '')[:250]}...")
        print("=" * 80 + "\n")
        return

    print("\n" + "=" * 95)
    print(f" YuE2 Saved Presets ({len(data)} presets in presets.json)")
    print("=" * 95)
    fmt = "{:<35} {:<12} {:<12} {:<12} {:<10} {:<8}"
    print(fmt.format("Preset Name", "Sem Temp", "Score Temp", "Rep Pen", "Steps", "Seed"))
    print("-" * 95)
    for name, p in data.items():
        print(fmt.format(
            name[:34],
            f"{p.get('sem_temp', 1.0):.2f}",
            f"{p.get('score_temp', 0.7):.2f}",
            f"{p.get('rep_pen', 1.1):.2f}",
            str(p.get('flow_steps', 16)),
            str(p.get('seed', 404))
        ))
    print("-" * 95)
    print("💡 To view details: ./manage_songs.py presets <name>")
    print("💡 In browser:      http://127.0.0.1:7860 (Creation Studio -> Preset controls)\n")


def main():
    parser = argparse.ArgumentParser(
        description="YuE2 Track Manager - See, Play, Rename & Manage Songs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list
    p_list = subparsers.add_parser("list", aliases=["ls"], help="List all songs")
    p_list.set_defaults(func=cmd_list)

    # presets
    p_pre = subparsers.add_parser("presets", help="List or view saved presets with all parameters")
    p_pre.add_argument("name", nargs="?", default=None, help="Preset name to view details")
    p_pre.set_defaults(func=cmd_presets)

    # play
    p_play = subparsers.add_parser("play", help="Play audio (defaults to most recent)")
    p_play.add_argument("track", nargs="?", default=None, help="Track name, substring, or list index #")
    p_play.add_argument("-t", "--time", type=float, default=None, help="Playback limit in seconds (preview)")
    p_play.add_argument("-v", "--volume", type=float, default=None, help="Volume level (0.0 to 1.0)")
    p_play.add_argument("--system", action="store_true", help="Play in macOS default player (QuickTime)")
    p_play.set_defaults(func=cmd_play)

    # rename
    p_rename = subparsers.add_parser("rename", aliases=["mv"], help="Rename a track")
    p_rename.add_argument("old_name", help="Current track name or list index #")
    p_rename.add_argument("new_name", help="New track name")
    p_rename.set_defaults(func=cmd_rename)

    # info
    p_info = subparsers.add_parser("info", help="Show track metadata, score, and lyrics")
    p_info.add_argument("track", nargs="?", default=None, help="Track name or list index #")
    p_info.set_defaults(func=cmd_info)

    # finder
    p_finder = subparsers.add_parser("finder", aliases=["open"], help="Open track in macOS Finder")
    p_finder.add_argument("track", nargs="?", default=None, help="Track name or list index #")
    p_finder.set_defaults(func=cmd_finder)

    # clean
    p_clean = subparsers.add_parser("clean", help="Remove empty test directories")
    p_clean.set_defaults(func=cmd_clean)

    args = parser.parse_args()

    if not args.command:
        # Default action when run without arguments: list tracks
        cmd_list(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
