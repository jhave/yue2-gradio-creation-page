# YuE2 Studio — Parameter Guidance

Three stages generate in sequence. Each control acts in exactly one of them.

| Stage | What it produces | Cost (M4, 16 flow steps) | Controlled by |
|---|---|---|---|
| **1 · Score (ABC)** | Symbolic notation: key, tempo, chord changes, bar structure | ~1,400 tokens @ ~25 tok/s ≈ 55s | Harmonic Exploration, Score Top-P, Score Repetition |
| **2 · Semantic (codec)** | The audio content: timbre, vocal delivery, arrangement | ~4,400 tokens @ ~15 tok/s ≈ 285s | Vocal Wildness, Semantic Top-P, Vocal Repetition, CFG |
| **3 · Flow solve + VAE** | Decodes codec tokens to 48 kHz waveform | linear in step count | Flow Steps |

**Synthesize Audio** with an ABC already in the editor skips stage 1 entirely — the three
score controls do nothing on that path. They apply to **Plan Score Only** and to the first
half of **1-Click**.

---

## Stage 1 · Score

### Harmonic Exploration — ABC temperature
*Library default 0.7 · app default 0.75 · useful 0.6–0.95*

| Low (0.4–0.6) | High (1.0+) |
|---|---|
| Conventional diatonic progressions | Borrowed chords, modal mixture |
| Predictable 4- and 8-bar phrasing | Irregular bar counts |
| Tempo close to what the style prompt named | Tempo drifts from the prompt |

Past ~1.3 the ABC starts malforming and the section timings in the Sheet Music tab
become unreliable.

### Score Top-P — ABC nucleus sampling
*Library default 0.9 · useful 0.85–0.95*

Caps which next-note candidates are eligible before temperature picks among them.
A low top-p cancels out a high temperature — this is the brake to reach for when
Harmonic Exploration is producing invalid notation.

| Low (0.7–0.8) | High (0.95–1.0) |
|---|---|
| Only high-probability notes and chords survive | Full distribution tail available |
| Tight harmony, no odd intervals | Rare chord qualities appear |
| — | Combined with high temperature, this is where malformed ABC comes from |

### Score Repetition — ABC repetition penalty
*Library default 1.005 · useful 1.0–1.05*

**Keep this low.** Repetition is structural in music: repeated bars, returning choruses,
looping chord progressions. A penalty tuned for the codec stage (≈1.2) applied here
produces scores that never restate a theme and never resolve.

Lookback window: 100 tokens. Top-k for this stage: 30.

---

## Stage 2 · Semantic

### Vocal Wildness — semantic temperature
*Library default 1.0 · app default 1.15 · useful 1.0–1.3*

| Low (0.8–0.95) | High (1.2–1.4) |
|---|---|
| Steady delivery, cleaner pitch | Unstable pitch, more ad-lib and texture |
| More literal reading of the style prompt | Timbre drifts across the song |
| Less variance between seeds | Wide variance between seeds |

Past ~1.5: noise bursts and dropped words.

### Semantic Top-P — codec nucleus sampling
*Library default 0.95 · useful 0.9–0.97*

The reliable brake when Vocal Wildness is producing garbage: drop this before
dropping temperature, since it removes the improbable tail without flattening the
distribution that remains.

| Low (0.85–0.9) | High (0.98–1.0) |
|---|---|
| Conservative timbre, fewer artifacts | More texture |
| Less interesting texture | More artifacts |

### Vocal Repetition — semantic repetition penalty
*Library default 1.2 · app default 1.18 · useful 1.1–1.25*

Suppresses looping in the codec stream. At 1.0 the model can lock into a loop and
stay there. Above ~1.3 it starts refusing to return to material it has already used,
so choruses stop sounding like the same chorus.

Lookback window: 50 tokens. Top-k for this stage: 100.

### CFG Guidance — classifier-free guidance
*Library default 1.0 (off) · valid 0–20 · app slider 1.0–3.0*

**1.0 means off, not "minimum".** At exactly 1.0 the negative branch is skipped and
one forward pass runs. Any other value builds a negative-conditioned prefix and runs
a second pass, amplifying the difference toward the style prompt.

Consequences of moving off 1.0:
- Stage 2 takes roughly twice as long
- Tensors through the attention kernel double in width — this is the kernel that
  aborts under MPS, so crash probability rises
- Above ~2.0: over-saturated, brittle, prompt terms start dominating the mix

Useful values: 1.0, or 1.2–1.8 when genre adherence matters more than render time.

---

## Stage 3 · Decode

### Flow Steps — ODE solve steps (midpoint method)
*Library default 32 · app default 16 · slider 8–32*

No effect on composition — the notes and the performance are already fixed by the
time this runs. Purely decode fidelity, linear in time cost.

- **8** — audible smearing on transients and sibilance. Fine for auditioning an arrangement.
- **12–16** — working default.
- **32** — cleanest decode. Use for anything you intend to keep.

### Random Seed

Fix it when comparing parameters — otherwise you cannot tell a parameter effect from
sampling variance. Change it to reroll identical settings.

---

## Advanced — candidate pools, memory, length

Collapsed by default in the panel. These were fixed in `src/yue2/protocol.py` until now.

### Score Top-K / Semantic Top-K
*Library defaults 30 (score) and 100 (semantic)*

The filter chain in `sampling.py` runs **repetition penalty → temperature → top-k → top-p**.
Top-k is therefore a hard cap on how many candidates exist at all, applied before nucleus
sampling narrows further.

This is the difference that matters: **temperature flattens the distribution**, making
unlikely things likelier everywhere, including the tokens that carry diction. **Top-k
widens the pool without changing the relative odds** inside it. For melodic variety at
minimum cost to legibility, raise Score Top-K rather than either temperature.

| | Low | High |
|---|---|---|
| **Score Top-K** (5–200) | 15–25: a narrow set of next notes, very conventional lines | 60–90: wider intervallic choice, more surprising melodic contour, harmony still coherent |
| **Semantic Top-K** (5–400) | 40–60: tighter diction, clearer consonants — the legibility control | 150+: more timbral variety, more artifacts |

### Score / Vocal Penalty Window
*Library defaults 100 (score) and 50 (semantic) tokens*

How far back the repetition penalty looks. Short windows block immediate stutter while
permitting long-range restatement; long windows discourage reusing material across a
wider span.

Lower the Score Penalty Window to ~40 if you want themes to return but not stutter.

### Min / Max Length
*Library defaults 200 and 9000 tokens*

Min Length forbids the end token until that many tokens exist — a floor on song length.
Max Length is the ceiling, so the effective maximum duration. Prefix plus Max Length must
stay under the 24576 context or the pipeline refuses the request.

### Score Mode (`cot`)
*Library default `full`*

- **full** — chord-annotated ABC, then audio. Most harmonic control.
- **melody** — melody-only ABC, no chord symbols. Looser harmonic commitment; the audio
  stage invents its own harmonisation around the line.
- **off** — no score at all, straight to codec tokens. The Sheet Music tab stays empty and
  guidance defaults to 1.01. Least predictable, occasionally the most alive.

---

## Melodically wild, still legible

The instinct is to raise Vocal Wildness. That is the wrong control: the melody is decided
in the **score** stage, and Vocal Wildness only destabilises the **delivery** of whatever
melody already exists. Raising it buys wandering pitch and slurred words, not better tunes.

Melody lives in stage 1. Legibility lives in stage 2. Push them in opposite directions:

| Control | Value | Why |
|---|---|---|
| Score Top-K | **80** | wider intervallic choice — the actual melodic variety |
| Harmonic Exploration | **0.90** | more adventurous intervals and chord changes |
| Score Top-P | 0.95 | admits the rarer candidates top-k just made available |
| Score Repetition | 1.005 | themes still allowed to return |
| Score Penalty Window | 40 | blocks stutter, permits restatement |
| Vocal Wildness | **1.00** | steady delivery — do not raise this |
| Semantic Top-K | **60** | tightened diction, clear consonants |
| Semantic Top-P | 0.93 | trims the artifact tail |
| Vocal Repetition | 1.18 | unchanged |
| Score Mode | melody | the audio stage harmonises a wandering line for itself |

If words still blur, drop Semantic Top-K to 45 before touching anything else.
If the melody is still dull, raise Score Top-K to 110 before raising temperature.

---

## Where parameters are stored

Every render writes `track.json` beside the audio:

```json
{
  "folder": "2026-09-14_101432_fourth_world_lo-fi",
  "track_title": "fourth_world_lo-fi",
  "preset": "fourth world, lo-fi",
  "created": "2026-09-14T10:14:32",
  "favorite": true,
  "style": "...",
  "lyrics": "...",
  "parameters": {
    "score_temp": 0.9,
    "score_top_p": 0.95,
    "score_rep_pen": 1.005,
    "score_top_k": 80,
    "score_pen_win": 40,
    "sem_temp": 1.0,
    "sem_top_p": 0.93,
    "rep_pen": 1.18,
    "sem_top_k": 60,
    "sem_pen_win": 50,
    "sem_min_tokens": 200,
    "sem_max_tokens": 9000,
    "cfg_scale": 1.0,
    "flow_steps": 32,
    "seed": 404,
    "cot_mode": "melody"
  },
  "render_seconds": 341.2,
  "audio_seconds": 131.4
}
```

The Track Library tab reads it back under **Prompt, Parameters & Lyrics Used**.
With `mutagen` installed (`pip install mutagen`) the same values are written into
the FLAC's Vorbis comments, so the audio file carries them when moved out of
its folder.

---

## Starting points

| Goal | H.Expl | Score-P | Score Rep | Score-K | Vocal Wild | Sem-P | Vocal Rep | Sem-K | CFG | Steps |
|---|---|---|---|---|---|---|---|---|---|---|
| Audition fast | 0.75 | 0.90 | 1.005 | 30 | 1.10 | 0.95 | 1.18 | 100 | 1.0 | 8 |
| Conventional, clean | 0.60 | 0.85 | 1.00 | 20 | 0.95 | 0.92 | 1.15 | 60 | 1.4 | 32 |
| **Melodically wild, legible** | 0.90 | 0.95 | 1.005 | 80 | 1.00 | 0.93 | 1.18 | 60 | 1.0 | 32 |
| Loose vocal, solid structure | 0.70 | 0.90 | 1.005 | 40 | 1.30 | 0.93 | 1.20 | 100 | 1.0 | 32 |
| Maximum strangeness | 1.25 | 0.98 | 1.05 | 150 | 1.40 | 0.98 | 1.25 | 250 | 1.0 | 32 |

Change one control at a time with the seed fixed. Two changes at once and the
attribution is lost.

---

## Favorites

The ☆/★ button beside either player writes `"favorite": true` into that track's
`track.json`, so the star survives renaming and travels with the folder — there is no
separate database to fall out of sync.

- **★ favorites only** filters the library list and table.
- **📄 Export favorites list** writes `outputs/favorites.json`: every starred track with
  its title, preset, audio path, duration, key/BPM, style prompt, lyrics and full
  parameters. That file is the input for the favorites webpage.

To star a track rendered before this existed, select it in the Library tab and click the
star — a `track.json` is created with the star set and empty parameters.

---

## Disk: archive format

The pipeline writes 24-bit FLAC — about **11 MB per minute**, so a four-minute
track costs 44 MB, and 23 tracks filled 1.2 GB in a day.

`latent.npy` is **0.4 MB per minute**, and `pipe.decode(latents)` is deterministic:
same latents, same waveform, no sampling. So **MP3 + latents is a complete archive** —
the lossless master is regenerable exactly, in seconds, without re-running generation.

| Archive format | 4-minute track | Lossless recoverable |
|---|---|---|
| flac (lossless) | 44 MB | it *is* the master |
| flac + mp3 | 54 MB | yes |
| **mp3 320 + latents** (default) | **11 MB** | yes, from latent.npy |
| mp3 192 + latents | 7 MB | yes, from latent.npy |

Set it in the dropdown beside the folder-name toggle. **🎧 Rebuild lossless** in the
Library tab regenerates the 24-bit FLAC from `latent.npy` — VAE decode only.

MP3 encoding goes through libsndfile (1.2.2 writes MP3 directly), with ffmpeg as
fallback. If neither is available the FLAC is kept and the status line says so.

### Reclaiming what is already on disk

```
python3 reclaim_space.py                    # report only
python3 reclaim_space.py --apply            # drop duplicate FLACs
python3 reclaim_space.py --mp3 320          # report the MP3 saving too
python3 reclaim_space.py --mp3 320 --apply  # convert and prune
```

Two independent savings: every folder before 2026-09-14 holds two real copies of the
same audio (`audio.flac` and `<folder>.flac`), and the surviving copy can become MP3.
Folders without `latent.npy` are skipped unless `--force`, since conversion would then
be irreversible. Dry run by default.

---

## Star Analysis

The fourth tab compares starred renders against the rest — parameters, prompt shape,
lyric shape, prompt vocabulary. It rebuilds whenever you star something or open the tab,
and `python3 analysis.py` prints the same report to the terminal.

The statistic is P(a random starred value > a random unstarred value), ties at half.
Rank-based, no distribution assumed, meaningful at small n. 0.50 means no signal.
Directional labels are withheld until both groups hold at least 5 tracks.

**The trap it is built to avoid:** a starred track is not a random sample. It is
something you chose to make *and* chose to keep. If you always render at 0.7
Exploration, the starred set shows 0.7 whether or not 0.7 helps. So every parameter is
reported twice — the spread across all renders, and the spread across starred ones —
and any parameter that never moved is listed under **Never varied** instead of being
given a spurious reading. The "What to vary next" section names the first of those.

**💾 Save FAVS preset** writes `FAVS-<date>` from the median of every starred track's
settings. Seed is deliberately excluded — a median seed means nothing.

The reading to trust soonest is **Prompt vocabulary**: it needs no parameter variance to
be informative, because your prompts already vary freely.

---

## Known failure mode

PyTorch's MPS scaled-dot-product-attention kernel can submit a command buffer that
Metal's validator rejects, and Metal calls `abort()` rather than raising. The whole
Python process dies mid-render; the progress bar stops and the browser tab goes
unreachable.

Confirm with:

```
ls -lt ~/Library/Logs/DiagnosticReports | head -3
```

A fresh `Python-*.ips` whose crashing thread is `metal gpu stream` is this bug, not
anything in `app.py`. Reduces its likelihood: shorter lyrics, CFG at 1.0, fewer
concurrent GPU applications, a newer torch.
