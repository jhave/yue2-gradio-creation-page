"""
Hover text for every control in the studio.

Kept out of app.py because it is prose, not program: ~100 lines of measured
description that changes when the model's behaviour is re-measured, not when
the interface changes. Each key is the elem_id of the component it documents;
assets/studio.js binds them by that id.
"""

TOOLTIPS = {
    "tip-score-temp": "ABC temperature. Low (0.4-0.6): conventional diatonic progressions, "
                      "predictable phrasing. High (1.0+): borrowed chords, modal mixture, "
                      "irregular bar counts. Above ~1.3 the notation starts malforming. "
                      "Library default 0.7.",
    "tip-score-top-p": "Nucleus sampling on the score. Low (0.7-0.8) keeps only the likeliest "
                       "notes and cancels out a high temperature. High (0.95-1.0) admits rare "
                       "chord qualities. Library default 0.9.",
    "tip-score-rep": "Repetition penalty on the notation. Keep near 1.005: repetition is "
                     "structural in music, and a codec-strength penalty here produces scores "
                     "that never restate a theme or resolve.",
    "tip-sem-temp": "Codec temperature — delivery, not melody. Low (0.8-0.95): steady pitch, "
                    "literal reading of the style prompt. High (1.2-1.4): unstable pitch, ad-lib, "
                    "timbre drift. Above ~1.5: dropped words. This is the control that costs "
                    "legibility — raise Score Top-K instead for melodic variety.",
    "tip-sem-top-p": "Nucleus sampling on the audio stage. Drop to 0.9 before dropping "
                     "temperature: it removes the improbable tail without flattening what "
                     "remains. Library default 0.95.",
    "tip-sem-rep": "Repetition penalty on the codec stream. At 1.0 the model can lock into a "
                   "loop. Above ~1.3 it refuses to reuse material, so choruses stop sounding "
                   "like the same chorus. Library default 1.2.",
    "tip-flow": "ODE solve steps. No effect on composition — the notes and the performance are "
                "already fixed by the time this runs. Default 12 renders a fast preview; the "
                "Library's Re-solve button upgrades a keeper to 32 without re-running generation.",
    "tip-cfg": "Classifier-free guidance. 1.0 means OFF — one forward pass. Any other value "
               "runs a second negative-conditioned pass: stronger genre adherence, roughly "
               "double the time for the audio stage, and double the tensor width through the "
               "attention kernel that aborts under MPS.",
    "tip-seed": "Fix it when comparing parameters, or you cannot tell a parameter effect from "
                "sampling variance. Change it to reroll identical settings.",
    "tip-score-topk": "How many note candidates survive before Top-P. Raising 30 to 60-90 "
                      "widens melodic choice WITHOUT flattening the distribution — more varied "
                      "melody at far less cost to legibility than raising temperature. "
                      "Library default 30.",
    "tip-sem-topk": "Candidate pool for the audio stage. Lowering 100 to ~50 tightens diction "
                    "and consonant clarity. Raise it only alongside a lower temperature.",
    "tip-score-win": "How many recent tokens the score repetition penalty looks back over. "
                     "Shorter windows permit long-range restatement while still discouraging "
                     "immediate stutter. Library default 100.",
    "tip-sem-win": "Lookback for the vocal repetition penalty. A longer window discourages "
                   "reusing phrases across a wider span; a shorter one only blocks immediate "
                   "loops. Library default 50.",
    "tip-minlen": "The end token is forbidden until this many tokens have been generated — "
                  "a floor on song length. Library default 200.",
    "tip-maxlen": "Token ceiling for the audio stage, so the effective maximum song length. "
                  "Library default 9000. Prefix plus this must stay under the 24576 context.",
    "tip-cot": "full: chord-annotated score, then audio. melody: melody-only score, no chord "
               "symbols — looser harmonic commitment. off: no score at all, straight to audio "
               "(the score panel below stays empty).",
    "tip-favs-only": "Minimum rating for the list, the table and the ⏮/⏭ playlist. "
                     "\"unrated\" shows only what you have not judged yet — the queue to work through.",
    "tip-rating": "0-5. Absent is not zero: leaving a track unrated means unjudged, and the "
                  "analysis excludes it. A 0 means you listened and rejected it, which is real "
                  "evidence. 4 or more counts as a favorite for the playlist and the page.",
    "tip-rating-lib": "0-5. Absent is not zero: unrated means unjudged and is excluded from the "
                      "analysis; 0 means listened to and rejected. 4+ counts as a favorite.",
    "tip-show-all-presets": "Off: factory presets, presets with no rated renders, and the best 25 "
                            "scoring 3.0 or higher. On: every preset in the file.",
    "tip-migrate": "Converts pre-rating stars to rating 4. Tracks that were never starred stay "
                   "UNRATED rather than becoming 0 — they were never judged, and recording them "
                   "as rejections would invent data.",
    "tip-append-tag": "Appends a short code for every setting that differs from the preset "
                      "baseline, e.g. [vw1.35_stk80]. Renders of one song at different values "
                      "stay distinguishable in Finder without the old 120-character names.",
    "tip-build-page": "Writes outputs/favorites_page/index.html with a player per starred "
                      "track, a fold-out parameter table and your notes. Audio is converted to "
                      "MP3 beside it, so the folder can be moved or uploaded whole. Rebuilding "
                      "preserves any intro text you edited into the page.",
    "tip-audio-format": "The pipeline writes 24-bit FLAC at about 11 MB per minute. "
                        "latent.npy is 0.4 MB per minute and the VAE decode is deterministic, "
                        "so MP3 + latents is a complete archive: the lossless master regenerates "
                        "exactly, in seconds, from Rebuild lossless in the Library.",
    "tip-prev": "Previous track in the list currently on screen — the favorites filter "
                "narrows the playlist as well as the table.",
    "tip-next": "Next track in the list currently on screen.",
    "tip-autoplay": "When a track finishes, load and play the next one.",
    "tip-star-report": "Rebuilds the comparison from every track.json on disk.",
    "tip-save-favs-preset": "Writes a preset named FAVS-<date> from the median of every "
                            "starred track's settings. A starting point, not a verdict.",
    "tip-upgrade": "Re-runs ONLY the flow solve, using the semantic tokens already on disk — the "
                   "autoregressive stage is skipped. Cost scales with audio LENGTH as well as step "
                   "count: on a 9000-token track the solve runs about 21s per step, so 32 steps is "
                   "roughly 11 minutes. On a short track it is under a minute.",
    "tip-upgrade-steps": "Flow steps for the re-solve. 32 is the library default and the cleanest "
                         "decode; the cost is roughly linear in this number.",
    "tip-redecode": "Rebuilds the 24-bit FLAC from latent.npy. VAE decode only — no regeneration, "
                    "no sampling, bit-identical to the original master.",
    "tip-display-name": "The name a listener sees. Used for the heading on the playlist page "
                        "and for the audio filename there — \"iridescent scaling\" becomes "
                        "audio/iridescent-scaling.mp3. The render folder keeps its own name.",
    "tip-notes": "Accompanying text for this track. Saved into its track.json and rendered "
                 "under the track on the playlist page.",
    "tip-export-favs": "Write outputs/favorites.json — every starred track with its audio path, "
                       "prompt, lyrics and parameters. This is the input for the favorites page.",
    "tip-stop": "Sets the flag the pipeline polls between tokens. Generation stops at the next "
                "token and the half-written folder is removed.",
    "tip-save": "Green means the current settings are already stored under this preset name. "
                "Blue means there is something unsaved.",
}

# Added with the P0 consolidation: the three generate actions used to be four
# buttons, two of which called the same function with the same arguments.
TOOLTIPS.update({
    "tip-generate": "Score then audio, in one pass, with the settings as they stand. "
                    "This is the button for making a song; the other two exist for "
                    "when you want to intervene between the two stages.",
    "tip-plan": "Stage 1 only, about 20 seconds: writes the ABC score into the panel "
                "below and stops. Edit it there, then press Synthesize from this score.",
    "tip-synth-score": "Stage 2 and 3 on whatever ABC is in the panel below — including "
                       "edits you made by hand. Skips score generation entirely.",
})

TOOLTIPS["tip-drop"] = (
    "Drop a request.json, a .abc score, a prompt.md, or several at once. A JSON "
    "carries a whole request — title, style, lyrics, score and every parameter it "
    "names — and an empty lyrics field in it means instrumental, so the box is "
    "cleared. A score goes to the panel below; a prompt file fills the style box, "
    "and the lyrics box too when it marks its lyrics with a heading or with "
    "[Section] tags."
)

TOOLTIPS["tip-drop-overwrite"] = (
    "On, a dropped file replaces what is in the boxes. That is usually what you "
    "want: a preset fills every box at startup, so without this a drop lands "
    "nowhere. Off, only empty boxes are filled and work in progress is safe."
)
