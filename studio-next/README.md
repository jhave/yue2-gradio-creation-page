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

Render feedback shows total elapsed minutes and an expandable list of completed,
active, and remaining stages. Finish estimates use a range from at least three
comparable completed jobs with the same flow setting and planning mode; until
then the display says it is learning estimates. The engine's nonlinear progress
bar and heartbeat-derived flow count are not treated as an accurate time forecast.

The composer also has a **Sheet music** disclosure beneath ABC notation. Saved
tracks offer **View sheet music** in their score/settings section. The bundled
[abcjs 6.4.4](https://docs.abcjs.net/visual/render-abc-options.html) engraves the
score locally and works offline. Its license is in `web/abcjs_basic.LICENSE`.

## Library and playback

- New outputs appear on refresh, every 30 seconds, and when returning to the tab.
- All renders, favorites, and New & unrated separate ratings from unreviewed work.
- Demo selection uses an optional local `data/starter.json` and `media/` snapshot.
- Demo audio remains copied locally, while available source tracks supply current ratings and text.
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
and copying a mocked completed result. Additional checks verify honest timing,
history-based estimates, current favorites export, and privacy of the manifest.
Browser checks cover selective transfers, preserving the current composition,
engraved scores, and standalone audio playback. The first real model render
completed through the existing engine: a 259.9-second stereo MP3 and saved ABC
score appeared in the library, and the independent audio copy matched the
engine output by SHA-256. See `VALIDATION.md` for the recorded checks.

Use one render at a time across both interfaces. The existing Gradio studio's
Stop control handles cancellation. Multiple simultaneous editing sessions,
musical staves, and latent re-solving are not implemented here yet.

## Files and isolation

`server.py` serves the catalog and local UI; `render_bridge.py` validates and maps
composition settings; `render_worker.py` calls the existing engine. `web/` has no
package installation requirements; its notation library is bundled.
`render_progress.py` tracks observed stages. `publish_gallery.py` builds and
updates the independent listening page from templates in `listening/`.
`api-schema.json` is a portable baseline contract; local
setup captures the running engine's current schema in ignored `data/`.

Machine paths, music, scores, personal prompts, render logs, drafts, and workspace
state stay in ignored `data/`, `media/`, and `outputs/`. The repository's original
`app.py` and assets remain the original studio. The running demo installation
does not change when this separate checkout is committed or pushed.

See [PUBLISHING.md](PUBLISHING.md) for local updates and the glia.ca upload bundle.
