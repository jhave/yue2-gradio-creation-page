# yue2-gradio-creation-page

A local studio interface for the [YuE2](https://huggingface.co/m-a-p/YuE2-3B) music
model, and the apparatus around it: a rating system, a parameter analysis that
correlates what gets kept against how it was made, and a published page of the
keepers.

Runs on Apple Silicon (MPS). Nothing here calls a hosted service.

## The files

| | |
|---|---|
| `app.py` | the Gradio studio — Creation, Sheet Music (ABC), Track Library, Star Analysis |
| `analysis.py` | correlates 0–5 ratings against parameters, prompt shape, lyric shape and score metrics |
| `sweep.py` | renders one prompt at a series of values for a single parameter, everything else fixed |
| `backfill_metadata.py` | rebuilds `track.json` for renders made before the sidecar existed |
| `reclaim_space.py` | drops duplicate FLACs, converts to MP3 where latents allow lossless recovery |
| `publish.py` | builds `outputs/favorites_page/` from every track rated 4+ |
| `PARAMETERS.md` | what each parameter does, measured rather than assumed |
| `proposal_ABC-Midi_*.md` | the symbolic pipeline: LLM → ABC → YuE2, and LLM → ABC → MIDI |

## Run it

```bash
python3 app.py          # http://127.0.0.1:7860
```

## The loop

Render → rate 0–5 → the Star Analysis tab says which parameter has never moved →
`sweep.py` moves it → rate those → repeat.

The distinction the ratings depend on: **unrated is not zero.** A track you never
listened to is excluded from every comparison. A track you rated 0 is evidence.
Collapsing those two was the defect in the binary star this replaced — it counted
unheard renders as evidence against themselves.

## Storage

The pipeline writes 24-bit FLAC at ~11 MB per minute. `latent.npy` is ~0.4 MB per
minute and the VAE decode is deterministic, so **MP3 + latents is a complete
archive** — the lossless master regenerates exactly from the Library's *Rebuild
lossless* button. Default archive format is `mp3 320 + latents`.

## The page

`publish.py` writes a self-contained, servable folder:

```
outputs/favorites_page/
  index.html                    display names, fold-out parameters per track
  index.json                    the whole playlist
  audio/<slug>.mp3              named from the display name
  data/<slug>.json              per track: parameters, prompt, lyrics, notes
```

Nothing in it carries a render folder name. Editing `index.html` by hand is
expected; rebuilding preserves the intro block.

## Credits

YuE2 is by [m-a-p](https://huggingface.co/m-a-p). This repository is the interface
and the analysis around it, not the model.

## Layout

This repository is the only copy of the studio. It is not where it runs.

YuE2 needs its clone — `src/`, the `.venv` with `yue2_infer` installed
editable, the model weights, and `outputs/` where renders land. That clone is
upstream's repository and cannot be pushed to, so the studio files live here
and are symlinked into it:

```
YuE/                                  yue2-gradio-creation-page/
  app.py           -> ../yue2-gradio-creation-page/app.py
  tooltips.py      -> ...                                tooltips.py
  analysis.py      -> ...                                analysis.py
  presets.json     -> ...                                presets.json
  assets/studio.*  -> ...                                assets/studio.*
  .venv/  src/  outputs/   (stay in the clone)           docs/   patches/
```

Run it from the clone, where `.venv` and `outputs/` are:

```bash
cd /path/to/YuE && source .venv/bin/activate && python app.py
```

Editing either path edits the same file, so there is nothing to copy and
nothing to keep in sync. Two lines in `app.py` depend on this:

- `root_dir = Path(__file__).parent.resolve()` — parent before resolve, so it
  is the clone, where `.venv`, `src/` and `outputs/` are.
- `REPO_DIR = Path(__file__).resolve().parent` — resolve first, so it is this
  repository, and `publish.py` writes the page straight into `docs/` ready to
  commit.

`patches/` holds the three changes the studio needs in upstream's `src/`.
