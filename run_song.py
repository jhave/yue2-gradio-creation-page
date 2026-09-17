#!/usr/bin/env python3
"""
Simple runner for YuE2 on Apple Silicon (M4 / MPS) or CUDA.

Usage examples:
  # 1. Generate just the symbolic ABC score (very fast):
  python run_song.py --stage plan --style "acoustic indie folk, male vocal" --lyrics "[Verse]\nMorning sun through the trees\nCoffee cups and summer breeze"

  # 2. Generate full audio from the default example:
  python run_song.py --output outputs/my-first-song

  # 3. Generate audio using custom style and lyrics:
  python run_song.py --style "80s synthwave, energetic, male vocals, 120 bpm" --lyrics "[Verse]\nCity lights in the rearview mirror\nNight is young and the path is clearer" --output outputs/synthwave
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Auto-detect and switch to the project virtual environment if not already active
root_dir = Path(__file__).resolve().parent
venv_python = root_dir / ".venv" / "bin" / "python"
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

src_dir = root_dir / "src"
if src_dir.exists() and str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))


def main():
    parser = argparse.ArgumentParser(description="Generate music with YuE2 on Apple Silicon (MPS) or CUDA")
    parser.add_argument("--stage", choices=["plan", "audio"], default="audio",
                        help="'plan' generates only the symbolic ABC score; 'audio' generates full audio.")
    parser.add_argument("--style", type=str, default=None,
                        help="Musical style prompt (genre, vocal style, instruments, BPM)")
    parser.add_argument("--lyrics", type=str, default=None,
                        help="Song lyrics with [Verse], [Chorus] tags")
    parser.add_argument("--lyrics-file", type=Path, default=None,
                        help="Path to text file containing lyrics")
    parser.add_argument("--abc-file", type=Path, default=None,
                        help="Path to existing ABC score to guide melody/chords")
    parser.add_argument("--cot", choices=["full", "melody", "off"], default="full",
                        help="'full' plans chords+melody; 'melody' plans only melody; 'off' skips symbolic planning")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--output", type=Path, default=Path("outputs/song_output"),
                        help="Directory to save artifacts (audio.flac, score.abc, plan.json)")
    parser.add_argument("--device", default="auto", help="Device: 'auto', 'mps', 'cpu', or 'cuda'")
    parser.add_argument("--model", default="m-a-p/YuE2-3B", help="Hugging Face model repository")
    parser.add_argument("--vae", default="m-a-p/YuE2-Vae", help="Hugging Face VAE repository")
    args = parser.parse_args()

    # Load lyrics
    lyrics = args.lyrics
    if args.lyrics_file:
        lyrics = args.lyrics_file.read_text(encoding="utf-8")
    elif lyrics is None:
        # Default fallback sample
        sample_path = Path("examples/song.json")
        if sample_path.exists():
            data = json.loads(sample_path.read_text(encoding="utf-8"))
            lyrics = data.get("lyrics")
            if args.style is None:
                args.style = data.get("style")

    if not lyrics:
        parser.error("Please provide --lyrics or --lyrics-file.")

    style = args.style or "acoustic pop, acoustic guitar and piano, warm vocals, 90 bpm"

    request = {
        "style": style,
        "lyrics": lyrics,
        "cot": args.cot,
        "seed": args.seed,
    }

    if args.abc_file:
        request["abc"] = args.abc_file.read_text(encoding="utf-8")

    from yue2 import YuE2Pipeline

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"🎵 Initializing YuE2Pipeline on device: {args.device}...")
    print(f"🎸 Style:  {style}")
    print(f"📝 Lyrics preview:\n{lyrics[:120]}...\n")

    with YuE2Pipeline.from_pretrained(args.model, vae=args.vae, device=args.device) as pipe:
        if args.stage == "plan":
            print("🎼 Generating symbolic ABC plan...")
            plan = pipe.plan(**request)
            plan.save(args.output)
            print(f"\n✅ Symbolic plan generated successfully!")
            print(f"📄 ABC score saved to: {args.output / 'score.abc'}")
            if plan.abc:
                print("\n--- ABC Score Preview ---")
                print("\n".join(plan.abc.splitlines()[:25]))
                print("-------------------------\n")
        else:
            print("🎶 Generating song (Symbolic Plan -> Semantic Tokens -> Flow Matching -> VAE Audio)...")
            song = pipe(**request)
            song.save_artifacts(args.output)
            print(f"\n✅ Song generated successfully!")
            print(f"🎧 Audio saved to: {args.output / 'audio.flac'}")
            print(f"📄 ABC score saved to: {args.output / 'score.abc'}")
            print(f"📊 Timing: {song.timing}")


if __name__ == "__main__":
    main()
