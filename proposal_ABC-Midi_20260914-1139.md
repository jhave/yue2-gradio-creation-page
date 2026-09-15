# Symbolic Pipeline: ABC → MIDI → Synthesis

A proposal for importing frontier-LLM ABC notation into this studio, and for the
two destinations it can take.

---

## What YuE2's own planner emits

Read from `outputs/*/score.abc` before writing any of this, because the dialect
constrains Route A and it turned out to be more capable than expected:

```abc
X:1
T:
M:4/4
L:1/32
Q:1/4=75
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins   clef=treble name="Ins Melody"   snm="Inst."
K:D#m
% intro
V: Vocal
M:9/8
"D#maj7/F##"z32|"F##dim"a4a4"Gmaj7/D"g16|
V: Ins
M:5/8
Z|
```

| Feature | Supported |
|---|---|
| Voices | exactly two, named `Vocal` and `Ins` |
| Chord symbols | yes, quoted before the note: `"F##dim"a4` |
| Section markers | `%` comments: `% intro`, `% verse`, `% chorus` |
| Meter changes | **per voice, mid-stream** — `M:` inside a voice block |
| Note resolution | `L:1/32` or `L:1/16` |
| Multi-bar rest | `Z`, `Z3` |
| Lyrics (`w:`) | no — lyrics arrive through the prompt, not the score |

**Polymeter is already available.** One existing score runs 4/4, 9/8, 5/8 and 7/8
against each other across the two voices. It has never been driven deliberately —
the planner chose those on its own.

**The planner also emits malformed ABC.** In `cyber_hopkins_trap_1789288983/score.abc`:

```
M:F##dim"c4d4c6B3A3-|        ← chord string collided with a meter directive
M:C5/8                        ← not a meter
```

A validator pays for itself before any import happens, by catching this in YuE2's
own output.

---

## Route A — ABC → YuE2

The LLM writes the structure; YuE2 supplies timbre, performance and **sung vocals**.

Already half-built: the Sheet Music tab has an editable ABC box and *Synthesize
Audio from this Score*, and `pipe()` accepts `abc=` directly, skipping its own
planning stage. What is missing is everything between an LLM's output and a score
YuE2 will accept.

### Import steps

1. **Parse and report** — voice count, meters per voice, bars per voice, key,
   tempo, section markers. Flag bar-count mismatches between voices.
2. **Map to two voices** — pick which incoming voice becomes `Vocal`. The rest
   fold into `Ins` (merged as chords, or highest-note-wins), or are dropped. This
   is where a four-part LLM score loses material, and the mapping should be
   visible and editable, not silent.
3. **Normalize** — rewrite `L:` to 1/16 or 1/32, convert `%%score` and voice
   groupings away, strip decorations, ornaments, `w:` lyric lines, and anything
   else outside the dialect table above.
4. **Validate** — reject unbalanced bars, unclosed chord quotes, meter directives
   that collided with chord strings.
5. **Hand to the Creation tab** and synthesize as usual.

### Prompt template for the frontier model

Constraining the LLM up front is cheaper than repairing its output:

```
Write ABC notation for a song section. Constraints:
- Exactly two voices: V: Vocal clef=treble, and V: Ins clef=treble
- Header: X:1, M:, L:1/16, Q:1/4=<bpm>, K:
- Chord symbols as quoted strings immediately before the note: "Dm7"A4
- Mark sections with % comments: % intro, % verse, % chorus, % bridge, % outro
- Meter may change per voice mid-stream with an M: line inside that voice's block
- Every bar must sum exactly to its current meter
- No w: lyric lines, no decorations, no %%score, no more than two voices
Compose: <the actual request — polyrhythm, modal shift, bar counts>
```

### What this route is for

Structural control over YuE2's composition while keeping its vocal synthesis.
Bar arithmetic, deliberate metric modulation, modal plans, and harmonic motion
the planner would not choose on its own.

---

## Route B — ABC → MIDI → external synthesis

Unconstrained. All voices, any meters, no dialect restrictions.

- `abc2midi` (`brew install abcmidi`) handles more ABC than anything in Python
- `music21` as pip fallback
- Output: multi-track MIDI to `outputs/symbolic/<name>/`, one track per voice,
  plus the source ABC and the prompt that produced it

### On the honest limitation

SuperCollider, VSTs and MIDI do not produce anything resembling YuE2 or Suno
output, and no amount of pipeline engineering changes that. The gap is not
compositional, it is four other things:

| | MIDI + synthesis | Neural audio |
|---|---|---|
| **Sung lyrics** | nothing — vocal synthesis is a separate hard problem | native |
| **Performance micro-timing** | quantised unless hand-shaped | learned from recordings |
| **Timbre and room** | whatever the instrument gives you | mic, room, tape, bleed, all implied |
| **Mix glue** | your job | baked in |

A neural model is trained on *finished records*. It emits production, not
notation. A symbolic pipeline emits notation, and everything that makes a record
sound like a record has to be added afterward by a person.

So Route B is not a way to get Suno-like results through a different door. It is
for a different output: scores, stems, parts, control data, notation that a
person or a DAW finishes. Worth building for what it is; not worth expecting the
other thing from.

---

## Route C — the hybrid, and the reason to build both

The interesting work is neither route alone:

1. LLM writes a structurally deliberate score — polymetric, modally planned
2. Route B renders it to MIDI, then to instrumental stems in Max, SuperCollider
   or a DAW — the things neural models cannot be told to do precisely
3. The same score, mapped through Route A, goes to YuE2 with `cot=off` so it
   plans nothing and follows the given ABC
4. YuE2's vocal is kept; its instrumental bed is discarded or buried
5. Mix the externally rendered stems under that vocal

This is the only path to a sung lyric over a genuinely polymetric arrangement.
Neither system does it alone: YuE2 will not follow a four-voice 7-against-4 score,
and SuperCollider will not sing.

Structure is decoupled from timbre, and each layer does what it is actually good at.

---

## What gets stored

Every import writes the same sidecar shape the renders use, so the Star Analysis
tab can compare on one scale:

```json
{
  "source": "llm",
  "llm_prompt": "the compositional prompt given to the frontier model",
  "llm_model": "which model wrote it",
  "abc": "the raw imported notation",
  "abc_normalized": "what was actually sent to YuE2",
  "voices_dropped": ["Alto", "Bass"],
  "route": "A",
  "rating": null
}
```

With that in place the analysis answers a question worth having: **do
LLM-authored scores rate higher than YuE2's own planning?** Same rating scale,
same ears, measurable.

---

## Build order

1. **Validator** — standalone, run against existing `score.abc` files. Immediate
   value, no new UI, and it catches the malformed bars already on disk.
2. **Route B** — ABC → MIDI. Self-contained, no dialect negotiation, testable
   without touching the render path.
3. **Route A** — the voice mapper and normalizer. The delicate part, and the one
   that benefits from the validator already existing.
4. **Sidecar and analysis integration** — once there are imports to compare.
