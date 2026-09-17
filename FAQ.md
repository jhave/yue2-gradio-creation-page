# FAQ

Rendered into the studio's **FAQ** tab at startup. Edit this file, restart, done —
no Python involved.

### What is this?

A local interface for **YuE2**, an open music generation model by
[m-a-p](https://huggingface.co/m-a-p). The model is theirs; this repository is the
studio around it — presets, a 0–5 rating system, an analysis that correlates what
you keep against how it was made, and a publishing step.

- Model: **[m-a-p/YuE2-3B on Hugging Face](https://huggingface.co/m-a-p/YuE2-3B)**
- Organisation: **[m-a-p](https://huggingface.co/m-a-p)**
- Project page: **[map-yue.github.io](https://map-yue.github.io/)**
- Code: **[github.com/multimodal-art-projection/YuE](https://github.com/multimodal-art-projection/YuE)**

Nothing here calls a hosted service. Generation runs on this machine, on Apple
Silicon via MPS.

### How does a song get made?

Two stages, and the interface is shaped around them.

1. **Score.** The model writes ABC notation — key, chords, melody, bar structure.
   Roughly 20 seconds.
2. **Audio.** The model performs that score: timbre, vocal delivery, arrangement.
   Minutes, scaling with length.

**Generate song** runs both. **Write score** runs stage 1 and stops, so you can
read or edit the notation before committing to the render. **Synthesize from this
score** runs stage 2 on whatever ABC is in the panel — including your edits.

### Can I use a score or a prompt I wrote somewhere else?

Yes, two ways.

**Paste.** Open the score panel and paste ABC straight into the editor, then press
*Synthesize from this score*. Same for the style and lyrics boxes.

**Drop.** *Open files*, at the top of the Create tab, takes `.abc`, `.md` and
`.txt` — dragged in or chosen — and routes them by what they are:

- a **`.abc`** file (or any file starting `X:`) goes to the score panel, which
  opens; the track title is taken from the score's `T:` field, or the filename
- a **`.md`** or **`.txt`** file fills the style prompt, and the lyrics box too
  when the file says where the lyrics start — either a `## Lyrics` heading or the
  first `[Section]` tag. Without either marker the whole file is treated as style.

A box you have already typed in is left alone, and the status line says so.
Clear it and drop again to replace.

### Which parameters actually matter?

The short version:

- **Score exploration** changes the *composition*. Low is diatonic and
  predictable; above ~1.3 the notation starts malforming.
- **Vocal wildness** changes the *performance*, not the notes. Above ~1.5 it drops
  words. Raise **Score Top-K** instead for melodic variety — it widens choice
  without flattening the distribution, which costs far less legibility.
- **Flow steps** affect neither. They are the final decode. Render previews at 12
  and re-solve a keeper to 32 from the Library, without re-generating anything.
- **Seed** must be fixed when comparing parameters, or you cannot tell an effect
  from sampling variance.

`PARAMETERS.md` has the measured version. Every control in the studio also has
hover text.

### Why does the rating go to 5, and why is unrated not zero?

Because they are different facts. **A track you never listened to is not a track
you rejected.** Unrated is excluded from every comparison; 0 means you listened and
said no, which is real evidence. Collapsing the two counts unheard renders as
evidence against themselves.

4 or higher makes a track a favourite: it enters the playlist and the published
page.

### What does the Analysis tab tell me?

Which parameter you have never actually moved. It correlates your ratings against
parameters, prompt shape, lyric shape and score metrics, and the useful output is
usually a gap rather than a finding — a slider that has sat at its default across
every render you rated. `sweep.py` then moves that one parameter across a series
with everything else fixed, and you rate the results.

### Where do the files go?

`outputs/<track folder>/`, one folder per render, with a `track.json` sidecar
holding parameters, prompt, lyrics, rating and notes.

The pipeline writes 24-bit FLAC at ~11 MB per minute. `latent.npy` is ~0.4 MB per
minute and the VAE decode is deterministic, so **MP3 + latents is a complete
archive** — the lossless master regenerates exactly from *Rebuild lossless*. That
is why the default archive format is `mp3 320 + latents`.

### How do I publish the keepers?

*Build playlist page* in the Library, or `python3 publish.py` for the same code
path without starting the interface. It writes a self-contained
`outputs/favorites_page/` — audio, per-track JSON, and an `index.html` whose intro
block is preserved when you rebuild, so hand edits survive.

Give tracks a **display name** first. Without one the page falls back to the folder
name, which carries the parameter tag.

### The sheet music panel is blank.

The notation renderer ([abcjs](https://www.abcjs.net/)) is not vendored into this
repository. Put `abcjs-basic-min.js` in `assets/` and restart.

This is cosmetic. The ABC text, the section timings and synthesis all work without
it — only the engraved staves are missing.

### Why is nothing happening?

Generation is synchronous and slow. The status line under the buttons reports
progress; **Stop** cancels at the next checkpoint, not instantly. CFG above 1.0
runs a second conditioned pass — roughly double the time for the audio stage, and
double the tensor width through an attention kernel that aborts under MPS.

### Credits

YuE2 is by **[m-a-p](https://huggingface.co/m-a-p)** — see
[YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B) for the model card, license and
citation. This repository is the interface and the analysis around it, not the
model.
