#!/usr/bin/env python3
"""
Reclaim disk in outputs/.

Two independent savings:

  duplicates  Each folder holds audio.flac and <folder>.flac. Renders from
              2026-09-14 onward hard-link them (one inode, two names, no extra
              bytes); earlier ones are real copies. Removing the copy is free.

  mp3         24-bit FLAC costs ~11 MB per minute. latent.npy costs ~0.4 MB per
              minute and the VAE decode is deterministic, so MP3 + latents is a
              complete archive: app.py's "Rebuild lossless" regenerates the
              original master exactly. Folders without latent.npy are skipped
              unless --force.

Dry run by default. Nothing is deleted without --apply.

    python3 reclaim_space.py                      # report only
    python3 reclaim_space.py --apply              # drop duplicate FLACs
    python3 reclaim_space.py --mp3 320            # report the MP3 saving too
    python3 reclaim_space.py --mp3 320 --apply    # convert and prune
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

OUTPUTS = Path(__file__).resolve().parent / "outputs"


def mb(n):
    return f"{n / 1e6:8.1f} MB"


def encode_mp3(source, dest, bitrate):
    try:
        import soundfile as sf
        if "MP3" in sf.available_formats():
            data, rate = sf.read(str(source), always_2d=True)
            sf.write(str(dest), data, rate, format="MP3")
            if dest.exists() and dest.stat().st_size > 1024:
                return True
        dest.unlink(missing_ok=True)
    except Exception:
        dest.unlink(missing_ok=True)
    if shutil.which("ffmpeg"):
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
                            "-codec:a", "libmp3lame", "-b:a", bitrate, str(dest)],
                           check=True, timeout=900)
            return dest.exists() and dest.stat().st_size > 1024
        except Exception:
            dest.unlink(missing_ok=True)
    return False


def audio_seconds(folder):
    for name in ("result.json", "track.json"):
        f = folder / name
        if f.exists():
            try:
                value = json.loads(f.read_text()).get("audio_seconds")
                if value:
                    return float(value)
            except Exception:
                pass
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually delete and convert")
    ap.add_argument("--mp3", metavar="KBPS", nargs="?", const="320",
                    help="also convert FLAC to MP3 at this bitrate and drop the FLACs")
    ap.add_argument("--force", action="store_true",
                    help="convert even when latent.npy is missing (irreversible)")
    args = ap.parse_args()

    if not OUTPUTS.is_dir():
        sys.exit(f"No outputs directory at {OUTPUTS}")

    bitrate = f"{args.mp3}k" if args.mp3 else None
    dup_freed = mp3_freed = 0
    skipped = []

    print(f"{'folder':46}{'duplicate':>12}{'flac':>12}{'latents':>10}")
    print("-" * 80)

    for folder in sorted(p for p in OUTPUTS.iterdir() if p.is_dir()):
        if folder.name == "favorites_page":
            continue
        flacs = sorted(folder.glob("*.flac"))
        if not flacs:
            continue

        default = folder / "audio.flac"
        named = [f for f in flacs if f.name != "audio.flac"]
        has_latents = (folder / "latent.npy").exists()

        # --- duplicate copy ---
        dup_bytes = 0
        if default.exists() and named:
            same_inode = default.stat().st_ino == named[0].stat().st_ino
            if not same_inode:
                dup_bytes = default.stat().st_size
                if args.apply:
                    default.unlink()
                    try:
                        # keep both names available at no cost
                        import os
                        os.link(named[0], default)
                        dup_bytes = dup_bytes  # link is free
                    except OSError:
                        pass
                dup_freed += dup_bytes

        # --- mp3 conversion ---
        conv_bytes = 0
        if bitrate:
            if not has_latents and not args.force:
                skipped.append(folder.name)
            else:
                source = named[0] if named else default
                if source.exists():
                    # only the surviving copy: the duplicate is counted above
                    conv_bytes = source.stat().st_size
                    if not args.apply:
                        secs = audio_seconds(folder) or 0
                        conv_bytes -= int(secs * (int(args.mp3) * 1000 / 8)) if secs else 0
                    else:
                        dest = folder / f"{folder.name}.mp3"
                        if dest.exists() or encode_mp3(source, dest, bitrate):
                            for f in list(folder.glob("*.flac")):
                                f.unlink()
                            conv_bytes -= dest.stat().st_size
                        else:
                            print(f"  ! no MP3 encoder available; stopping")
                            conv_bytes = 0
                            bitrate = None
                    mp3_freed += max(conv_bytes, 0)

        print(f"{folder.name[:44]:46}{mb(dup_bytes):>12}{mb(conv_bytes):>12}"
              f"{('yes' if has_latents else '—'):>10}")

    print("-" * 80)
    total = dup_freed + mp3_freed
    verb = "freed" if args.apply else "would free"
    print(f"duplicate FLACs  {verb} {mb(dup_freed)}")
    if bitrate:
        print(f"MP3 {args.mp3}kbps    {verb} {mb(mp3_freed)}")
    print(f"TOTAL            {verb} {mb(total)}")

    if skipped:
        print(f"\nskipped (no latent.npy, conversion would be irreversible): {len(skipped)}")
        for name in skipped:
            print(f"  {name}")
        print("  re-run with --force to convert them anyway")

    if not args.apply:
        print("\nDry run. Re-run with --apply to make the changes.")


if __name__ == "__main__":
    main()
