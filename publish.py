#!/usr/bin/env python3
"""
Build outputs/favorites_page/ from every track rated 4 or higher.

Same code path as the studio's "Build playlist page" button, callable without
starting the interface.

    python3 publish.py
    python3 publish.py --title "kept takes" --subtitle "YuE2, September 2026"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", default="Favorites")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--no-mp3", action="store_true",
                    help="copy the audio as-is instead of converting")
    args = ap.parse_args()

    import app

    message, path = app.build_favorites_page(
        title=args.title, subtitle=args.subtitle, convert_mp3=not args.no_mp3
    )
    print(message)
    if path:
        print(f"\nopen {path}")
        unnamed = [t for t in app.get_track_data()
                   if t.get("rating") is not None and t["rating"] >= app.FAVORITE_THRESHOLD
                   and not app.read_track_metadata(Path(t["path"])).get("display_name")]
        if unnamed:
            print(f"\n{len(unnamed)} track(s) have no display name and fell back to the "
                  f"folder name. Set one in the Library tab:")
            for t in unnamed:
                print(f"  {t['name']}")


if __name__ == "__main__":
    main()
