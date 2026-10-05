# YuE2 Studio Next

A local listening and composition workspace with a shared player, a live output
library, playlists, ratings, drafts, and selective reuse of a track's prompts,
lyrics, ABC score, and sixteen generation settings. The existing Gradio app
remains available alongside it.

## Start

```sh
cd studio-next
python3 server.py --port 7861
```

Or double-click `Launch.command` on macOS. Open http://127.0.0.1:7861.
Python 3.10+ is sufficient for listening and draft editing. A fresh checkout
opens an empty library; personal audio and local workspace data are excluded
from Git.

Connect an existing output library without moving or changing any files:

```sh
python3 configure_studio.py --outputs /path/to/YuE/outputs
```

To enable Create, keep the original Gradio studio running on port 7860 and
configure its Python environment, which must contain `gradio_client`:

```sh
python3 configure_studio.py --outputs /path/to/YuE/outputs \
  --python /path/to/YuE/.venv/bin/python --overwrite
```

The setup command reads the running engine's API schema; it never submits a
render. `--studio` can select another engine URL. Restart Studio Next after
changing connection settings. This interface never loads its own model.

## Reuse while composing

Select a track to inspect it without changing playback. Use **Send to Create →**
beside its style prompt, lyrics, ABC score, or generation settings. The transfer
opens the composition view and changes only the selected part. Prompt and lyrics
are appended so existing writing remains. The transfer menu also sends settings
with a score or style prompt. Title and unrelated fields are kept; Undo restores
only the fields affected by the transfer.

**New draft from track** imports the whole composition after saving the current
one. **Copy** is available for manual reuse, and export is under More.

**Create** saves a draft and submits an explicit job to the existing engine. A
supplied ABC score selects synthesis; without one, the engine plans a new score
and then synthesizes. The engine writes a new render to its output directory,
and Studio Next copies the result into its own `outputs/`. It never replaces an
existing track. Listening continues; **Play new track** selects the result.

## Library and playback

- New outputs appear on refresh, every 30 seconds, and when returning to the tab.
- All renders, favorites, and New & unrated separate ratings from unreviewed work.
- Demo selection uses an optional local `data/starter.json` and `media/` snapshot.
- One player keeps its track and position across Library, Create, and Saved drafts.
- Playback continues across browser tabs by default; preferences offer pause on leaving.
- Reload restores the last track and position paused. Composition fields recover locally.
- Playlists, ratings, notes, and drafts are saved in this workspace's `data/state.json`.

## Validation and current limits

```sh
python3 -m unittest discover -s tests -v
node --check web/app.js
```

Checks cover new render discovery, copied-library fallback, isolated edits,
request validation, audio seeking, empty-checkout behavior, endpoint selection,
and copying a mocked completed result. Browser checks cover selective transfers
and preserving the current composition. Full model generation has not yet been
tested through this interface.

Use one render at a time across both interfaces. The existing Gradio studio's
Stop control handles cancellation. Multiple simultaneous editing sessions,
musical staves, and latent re-solving are not implemented here yet.

## Files and isolation

`server.py` serves the catalog and local UI; `render_bridge.py` validates and maps
composition settings; `render_worker.py` calls the existing engine. `web/` has no
frontend dependencies. `api-schema.json` is a portable baseline contract; local
setup captures the running engine's current schema in ignored `data/`.

Machine paths, music, scores, personal prompts, render logs, drafts, and workspace
state stay in ignored `data/`, `media/`, and `outputs/`. The repository's original
`app.py` and assets remain the original studio. The running demo installation
does not change when this separate checkout is committed or pushed.

See [PUBLISHING.md](PUBLISHING.md) for the planned independent glia.ca listening page.
