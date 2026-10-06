#!/usr/bin/env python3
"""Build a standalone listening page from current four- and five-star tracks."""
import argparse
from contextlib import nullcontext
import hashlib
import json
import shutil
import threading
import time
import zipfile
from pathlib import Path
try:
    import fcntl
except ImportError:
    fcntl = None

ROOT = Path(__file__).resolve().parent
PUBLISH_LOCK = threading.RLock()


def build(catalog, workspace, destination, title='Kept songs'):
    destination.mkdir(parents=True, exist_ok=True)
    with PUBLISH_LOCK, (destination / '.publisher.lock').open('a') as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _build(catalog, workspace, destination, title)
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _build(catalog, workspace, destination, title):
    with getattr(catalog, 'lock', nullcontext()):
        catalog.refresh(force=True)
        sources = list(catalog.items.values())
        try:
            state = json.loads((workspace / 'data/state.json').read_text())
        except (OSError, ValueError):
            state = {}
    tracks = []
    inputs = []
    for source in sources:
        track = {**source, **state.get('overrides', {}).get(source['id'], {})}
        if track.get('rating') not in (4, 5):
            continue
        audio = workspace / track['copied_audio'] if track.get('copied_audio') else Path(track['audio_path'])
        if not audio.is_file():
            continue
        name = track['id'] + audio.suffix.lower()
        public = {key: track.get(key, '') for key in ('id', 'title', 'rating', 'duration', 'created', 'style', 'lyrics')}
        public['audio'] = 'audio/' + name
        tracks.append(public)
        stat = audio.stat()
        inputs.append((audio, name, stat.st_size, stat.st_mtime_ns))
    tracks.sort(key=lambda t: (-t['rating'], t['title'].casefold()))
    templates = ROOT / 'listening'
    asset_files = sorted(p for p in templates.iterdir() if p.is_file())
    digest = hashlib.sha256(json.dumps({'title': title, 'tracks': tracks,
        'audio': [(n, size, modified) for _, n, size, modified in inputs],
        'assets': [(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in asset_files]}, sort_keys=True).encode()).hexdigest()
    signature = destination / '.build-signature'
    if signature.exists() and signature.read_text() == digest and (destination / 'index.html').exists() and all((destination / 'audio' / name).is_file() for _, name, _, _ in inputs):
        return {'count': len(tracks), 'changed': False, 'path': str(destination)}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'audio').mkdir(exist_ok=True)
    for audio, name, size, modified in inputs:
        copy = destination / 'audio' / name
        if not copy.exists() or copy.stat().st_size != size or copy.stat().st_mtime_ns != modified:
            temp = copy.with_suffix(copy.suffix + '.tmp')
            shutil.copy2(audio, temp)
            temp.replace(copy)
    for asset in asset_files:
        temp = destination / (asset.name + '.tmp')
        shutil.copy2(asset, temp)
        temp.replace(destination / asset.name)
    payload = {'title': title, 'tracks': tracks, 'updated': time.time()}
    for name, content in [('playlist.json', json.dumps(payload, ensure_ascii=False, indent=2)),
                          ('playlist.js', 'window.GLIA_PLAYLIST = ' + json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c') + ';\n')]:
        temp = destination / (name + '.tmp')
        temp.write_text(content, encoding='utf-8')
        temp.replace(destination / name)
    signature.write_text(digest)
    return {'count': len(tracks), 'changed': True, 'path': str(destination)}


def bundle(destination, archive):
    """Package only currently selected songs; retained older copies stay out."""
    playlist = json.loads((destination / 'playlist.json').read_text())
    names = ['index.html', 'listening.css', 'listening.js', 'playlist.json', 'playlist.js']
    names += [track['audio'] for track in playlist['tracks']]
    temp = archive.with_suffix(archive.suffix + '.tmp')
    with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name in names:
            output.write(destination / name, name)
    temp.replace(archive)


def watch(catalog, workspace, destination, interval=30):
    while True:
        try:
            result = build(catalog, workspace, destination)
            if result['changed']:
                print(f"Listening page updated: {result['count']} favorites · {destination}", flush=True)
        except Exception as exc:
            print(f"Listening page update will retry: {exc}", flush=True)
        time.sleep(interval)


def start_watcher(catalog, workspace, destination):
    thread = threading.Thread(target=watch, args=(catalog, workspace, destination), daemon=True)
    thread.start()
    return thread


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true', help='Update locally every 30 seconds')
    parser.add_argument('--zip', action='store_true', help='Create a fresh glia-page-upload.zip for upload')
    args = parser.parse_args()
    from server import Catalog
    destination = ROOT / 'glia-page'
    result = build(Catalog(), ROOT, destination)
    print(f"{result['count']} favorites · {destination / 'index.html'}")
    if args.zip:
        archive = ROOT / 'glia-page-upload.zip'
        bundle(destination, archive)
        print(f"Upload bundle: {archive}")
    if args.watch:
        watch(Catalog(), ROOT, destination)


if __name__ == '__main__':
    main()
