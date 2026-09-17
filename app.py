#!/usr/bin/env python3
"""
YuE2 Studio - Local Web UI
Advanced interactive studio for YuE2 on Apple Silicon (MPS) or CUDA.
Features:
 - Interactive ABC score editor with real-time section timing calculations
 - Fast Draft vs. Master Flow Steps slider (8 to 32 steps)
 - Temperature, Top-P, CFG, and Repetition Penalty controls
 - Interactive waveform audio player
 - Full Track Manager: easily see, rename, play, and reveal tracks in Finder
"""

import os
import sys
from pathlib import Path

# Disable upper limit for MPS allocations on Apple Silicon to prevent OOM
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")

# Ensure running inside .venv
# .parent before .resolve(): app.py is a symlink in the yue2 working folder,
# and root_dir must be that folder — where .venv, src/ and outputs/ live —
# not the repository the symlink points into.
root_dir = Path(__file__).parent.resolve()
venv_python = root_dir / ".venv" / "bin" / "python"
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

src_dir = root_dir / "src"
if src_dir.exists() and str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

import dataclasses
import math
import datetime
import html
import json
import re
import shutil
import subprocess
import time
import gradio as gr

from tooltips import TOOLTIPS

# Global pipeline instance
_PIPELINE = None


def get_pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        import torch
        from yue2 import YuE2Pipeline
        is_mps = torch.backends.mps.is_available()
        # offload_ar moves the whole AR stack to CPU for the flow-solve stage and back
        # again. On a discrete GPU that frees VRAM; on Apple Silicon the destination is
        # the same physical RAM, so it is two full copies of a 3B model for nothing.
        # Set YUE2_OFFLOAD_AR=1 to restore it if memory pressure appears.
        offload_ar = os.environ.get("YUE2_OFFLOAD_AR", "0") == "1"
        print(f"🎵 Initializing YuE2Pipeline in memory (MPS={is_mps}, offload_ar={offload_ar})...")
        _PIPELINE = YuE2Pipeline.from_pretrained(
            "m-a-p/YuE2-3B",
            vae="m-a-p/YuE2-Vae",
            device="auto",
            memory_budget_gib=24,
            offload_ar=offload_ar
        )
    return _PIPELINE


def parse_abc_metrics(abc_text: str) -> str:
    """Analyze ABC notation to calculate key, tempo, meters, and section durations."""
    if not abc_text or not abc_text.strip():
        return "No ABC score provided."

    tempo_m = re.search(r"Q:1/4=(\d+)", abc_text)
    tempo = int(tempo_m.group(1)) if tempo_m else 120
    meter_m = re.search(r"M:(\d+)/(\d+)", abc_text)
    beats_per_bar = int(meter_m.group(1)) if meter_m else 4
    key_m = re.search(r"K:([A-G][b#]?[m]?)", abc_text)
    key = key_m.group(1) if key_m else "Unknown"

    is_vocal = False
    curr_sec = "intro"
    sec_bars = {}

    for line in abc_text.splitlines():
        line = line.strip()
        if line.startswith("%"):
            curr_sec = line.lstrip("%").strip()
        elif line.startswith("V: Vocal"):
            is_vocal = True
        elif line.startswith("V: Ins"):
            is_vocal = False
        elif is_vocal and "|" in line and not line.startswith(("X:", "T:", "M:", "L:", "Q:", "K:", "V:")):
            bars = line.count("|")
            sec_bars[curr_sec] = sec_bars.get(curr_sec, 0) + bars

    bar_sec = (60.0 / tempo) * beats_per_bar
    total_bars = sum(sec_bars.values())
    total_sec = total_bars * bar_sec

    lines = [
        f"**Key**: `{key}` | **Meter**: `{beats_per_bar}/4` | **Tempo**: `{tempo} BPM` | **Estimated Length**: `{int(total_sec // 60)}m {int(total_sec % 60):02d}s` ({total_bars} bars)",
        "\n**Section Breakdown:**"
    ]
    for s, b in sec_bars.items():
        lines.append(f"- **`%{s}`**: {b} bars (~{b * bar_sec:.1f}s)")

    return "\n".join(lines)


# ==================== PREVIEW ====================
# Committing 30 minutes to hear whether a score works is the wrong trade. A
# preview renders the opening 20 seconds at the cheapest flow setting: the AR
# stage is the expensive one and it scales with how much score it is given, so
# cutting the score is what makes this fast, not cutting the solver.

PREVIEW_SECONDS = 20.0
PREVIEW_FLOW_STEPS = 8

_VOICE_RE = re.compile(r"^V:\s*(\S+)")
_METER_RE = re.compile(r"^M:\s*(\d+)/(\d+)")
_MULTIREST_RE = re.compile(r"^\s*Z(\d*)\s*$")


def _bar_seconds(meter, tempo):
    """Seconds per bar. 6/4 at 120 is 3.0s; 7/8 is 1.75s; 9/8 is 2.25s."""
    num, den = meter
    return (60.0 / tempo) * num * (4.0 / den)


def _bars_in(token):
    """`Z3` is three bars of rest in one token; everything else is one bar."""
    m = _MULTIREST_RE.match(token.replace("|", ""))
    return int(m.group(1) or 1) if m else 1


def _tokens_of(line):
    """A bar line into its bars, each keeping its trailing '|'."""
    return [t + "|" for t in line.split("|") if t.strip()]


def split_abc(abc_text):
    """
    The score into (header, blocks). A block is one V: section: the voice line,
    any mid-stream M:, and its bar tokens, with the % section marker above it.
    """
    lines = abc_text.splitlines()
    k = -1
    for i, l in enumerate(lines):
        if l.strip().startswith("K:"):
            k = i
            break
    head, body = (lines[:k + 1], lines[k + 1:]) if k >= 0 else ([], lines)

    blocks, cur, pre = [], None, []
    for line in body:
        s = line.strip()
        if s.startswith("%"):
            pre.append(line)
            continue
        m = _VOICE_RE.match(s)
        if m:
            if cur:
                blocks.append(cur)
            cur = {"pre": pre, "voice": m.group(1), "head": [line], "bars": []}
            pre = []
            continue
        if cur is None:
            head.append(line)
            continue
        if _METER_RE.match(s):
            cur["head"].append(line)
        elif "|" in s:
            cur["bars"].extend(_tokens_of(line))
        else:
            cur["head"].append(line)
    if cur:
        blocks.append(cur)
    return head, blocks


def _measure_blocks(blocks, head_meter, tempo):
    """Give every block its meter and duration, following mid-stream M: changes."""
    meters = {}
    for b in blocks:
        for l in b["head"]:
            m = _METER_RE.match(l.strip())
            if m:
                meters[b["voice"]] = (int(m.group(1)), int(m.group(2)))
        b["meter"] = meters.get(b["voice"], head_meter)
        b["bar_sec"] = _bar_seconds(b["meter"], tempo)
        b["seconds"] = sum(_bars_in(t) for t in b["bars"]) * b["bar_sec"]


def truncate_abc(abc_text, seconds=PREVIEW_SECONDS):
    """
    The opening `seconds` of a score, cut at a bar line.

    Voices share one timeline but not one block length — a voice resting under
    another writes `Z3|`, three bars in a single token — so each voice is cut
    at its own bar that crosses `seconds`, not at a matching token count.

    Returns (abc, kept_seconds, whole_seconds).
    """
    if not (abc_text or "").strip():
        return abc_text, 0.0, 0.0

    tm = re.search(r"Q:1/4=(\d+)", abc_text)
    tempo = int(tm.group(1)) if tm else 120
    head, blocks = split_abc(abc_text)
    hm = _METER_RE.search("\n".join(head)) or re.search(r"^M:\s*(\d+)/(\d+)", abc_text, re.M)
    head_meter = (int(hm.group(1)), int(hm.group(2))) if hm else (4, 4)
    _measure_blocks(blocks, head_meter, tempo)

    voices = {b["voice"] for b in blocks}
    totals = {v: sum(b["seconds"] for b in blocks if b["voice"] == v) for v in voices}
    whole = max(totals.values()) if totals else 0.0
    if whole <= seconds:
        return abc_text, whole, whole

    out = list(head)
    elapsed = {v: 0.0 for v in voices}
    for b in blocks:
        v = b["voice"]
        if all(e >= seconds for e in elapsed.values()):
            break
        if elapsed[v] >= seconds:
            continue
        take = []
        for tok in b["bars"]:
            if elapsed[v] >= seconds:
                break
            take.append(tok)
            elapsed[v] += _bars_in(tok) * b["bar_sec"]
        if not take:
            continue
        out.extend(b["pre"])
        out.extend(b["head"])
        out.append("".join(take))
    return "\n".join(out) + "\n", max(elapsed.values()), whole


# ==================== DROPPED FILES ====================
# Scores and prompts written elsewhere — a .abc from notation software, a
# prompt.md from a text editor — go in by drag and drop or by paste. The
# routing is by what the file is, not by which box it was dropped on.

# A lyric sheet announces itself: either a markdown heading that says lyrics, or
# the [Section] tags the model reads. Everything before that is the style prompt.
_LYRICS_HEADING = re.compile(r"^#{1,6}\s*(lyrics?|words|song)\s*:?\s*$",
                             re.IGNORECASE | re.MULTILINE)
_SECTION_TAG = re.compile(r"^\s*\[[^\]\n]{2,60}\]\s*$", re.MULTILINE)
_STYLE_HEADING = re.compile(r"^#{1,6}\s*(style|prompt|production)\b.*$",
                            re.IGNORECASE | re.MULTILINE)


def split_prompt_document(text):
    """
    A prompt document into (style, lyrics).

    Three shapes, in order of how explicitly they say what they are:
      1. a '## Lyrics' heading          -> split there
      2. a first [Section] tag          -> split there
      3. neither                        -> the whole file is the style prompt
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return "", ""

    m = _LYRICS_HEADING.search(text)
    if m:
        style, lyrics = text[:m.start()], text[m.end():]
    else:
        m = _SECTION_TAG.search(text)
        if m:
            style, lyrics = text[:m.start()], text[m.start():]
        else:
            style, lyrics = text, ""

    # A leading '# Style' or '# Prompt' heading is a label for the box the text
    # is about to land in, so it is dropped rather than sent to the model.
    style = _STYLE_HEADING.sub("", style, count=1)
    return style.strip(), lyrics.strip()


def abc_title(abc_text):
    """The ABC T: field, which is the piece's own name for itself."""
    for line in (abc_text or "").splitlines():
        if line.startswith("T:"):
            return line[2:].strip()
    return ""


# request.json nests the sampling settings; the sliders are flat. One map, used
# in both directions of reading, so a file written by this studio round-trips.
_REQUEST_TO_PARAM = {
    ("abc_sampling", "temperature"): "score_temp",
    ("abc_sampling", "top_p"): "score_top_p",
    ("abc_sampling", "repetition_penalty"): "score_rep_pen",
    ("abc_sampling", "top_k"): "score_top_k",
    ("abc_sampling", "penalty_window"): "score_pen_win",
    ("semantic_sampling", "temperature"): "sem_temp",
    ("semantic_sampling", "top_p"): "sem_top_p",
    ("semantic_sampling", "repetition_penalty"): "rep_pen",
    ("semantic_sampling", "top_k"): "sem_top_k",
    ("semantic_sampling", "penalty_window"): "sem_pen_win",
    ("semantic_sampling", "min_tokens"): "sem_min_tokens",
    ("semantic_sampling", "max_tokens"): "sem_max_tokens",
}

# Top-level keys that are parameters rather than text. `cot` is what the model
# library calls it; `cot_mode` is what a preset calls it. `ode_steps` is the
# pipeline's name for flow steps.
_REQUEST_TOP_PARAM = {
    "seed": "seed", "cfg_scale": "cfg_scale",
    "cot": "cot_mode", "cot_mode": "cot_mode",
    "flow_steps": "flow_steps", "ode_steps": "flow_steps",
}


def parse_request_json(text):
    """
    A request.json (or a preset, or a track.json) into (fields, params).

    `fields` holds only the keys the file actually names, so an absent key
    leaves its box alone while `"lyrics": ""` deliberately empties it — that
    is how an instrumental says it is one.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object at the top level")

    fields, params = {}, {}

    for key, dest in (("id", "title"), ("title", "title"), ("track_title", "title"),
                      ("style", "style"), ("lyrics", "lyrics"), ("abc", "abc")):
        if key in data and data[key] is not None:
            fields.setdefault(dest, str(data[key]))

    for key, pkey in _REQUEST_TOP_PARAM.items():
        if key in data and data[key] is not None:
            params[pkey] = data[key]

    for (block, key), pkey in _REQUEST_TO_PARAM.items():
        section = data.get(block)
        if isinstance(section, dict) and section.get(key) is not None:
            params[pkey] = section[key]

    # track.json and presets keep a flat dict already keyed the way the sliders are.
    flat = data.get("parameters")
    if isinstance(flat, dict):
        for pkey in PARAM_KEYS:
            if flat.get(pkey) is not None:
                params[pkey] = flat[pkey]
    for pkey in PARAM_KEYS:
        if data.get(pkey) is not None:
            params[pkey] = data[pkey]

    coerced = {}
    for pkey, value in params.items():
        cast = PARAM_CASTS.get(pkey, str)
        try:
            coerced[pkey] = cast(value) if cast is not str else str(value).strip()
        except (TypeError, ValueError):
            pass
    return fields, coerced


def load_dropped_files(files, current_title, current_style, current_lyrics,
                       current_abc, overwrite=True):
    """
    Read dropped or chosen files into the right fields.

      *.json             -> a whole request: title, style, lyrics, score, sliders
      *.abc, *.abc.txt   -> the score panel, and the panel opens
      *.md, *.txt        -> style prompt, plus lyrics when the file marks them

    With `overwrite` on, a dropped file wins over what is in the boxes. It has
    to: a preset is loaded into every box at startup, so "fill only the empty
    ones" meant a drop landed nowhere. Turn it off to protect work in progress.
    The track title is taken from the JSON id, the ABC T: field, or the filename.
    """
    blank_params = [gr.update() for _ in PARAM_KEYS]
    if not files:
        return tuple([gr.update(), gr.update(), gr.update(), gr.update(),
                      gr.update(), ""] + blank_params)

    paths = [Path(f if isinstance(f, str) else getattr(f, "name", str(f)))
             for f in (files if isinstance(files, list) else [files])]

    new_abc, new_style, new_lyrics, title_from = None, None, None, ""
    new_params = {}
    read, skipped = [], []

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped.append(f"{path.name} ({exc.strerror or 'unreadable'})")
            continue

        # .abc.txt is what a browser download of a score is often called.
        is_abc = (path.suffix.lower() == ".abc"
                  or path.name.lower().endswith(".abc.txt")
                  or bool(re.match(r"^\s*X:\s*\d", text)))

        if path.suffix.lower() == ".json":
            try:
                fields, params = parse_request_json(text)
            except (ValueError, json.JSONDecodeError) as exc:
                skipped.append(f"{path.name} (not readable JSON: {exc})")
                continue
            if "abc" in fields:
                new_abc = fields["abc"].strip()
            if "style" in fields:
                new_style = fields["style"].strip()
            if "lyrics" in fields:
                # Present but empty is a statement, not an omission: instrumental.
                new_lyrics = fields["lyrics"].strip()
            if "title" in fields and fields["title"].strip():
                title_from = fields["title"].strip()
            new_params.update(params)
            landed = [n for n, v in (("title", "title" in fields),
                                     ("style", "style" in fields),
                                     ("lyrics", "lyrics" in fields),
                                     ("score", "abc" in fields)) if v]
            if params:
                landed.append(f"{len(params)} parameter"
                              f"{'s' if len(params) != 1 else ''}")
            read.append(f"`{path.name}` → " + (" + ".join(landed) or "nothing recognisable"))
        elif is_abc:
            new_abc = text.strip()
            title_from = abc_title(new_abc) or path.stem.replace(".abc", "")
            read.append(f"`{path.name}` → score")
        elif path.suffix.lower() in (".md", ".txt"):
            style, lyrics = split_prompt_document(text)
            if style:
                new_style = style
            if lyrics:
                new_lyrics = lyrics
            where = " + ".join(w for w, v in (("style", style), ("lyrics", lyrics)) if v)
            read.append(f"`{path.name}` → {where or 'nothing recognisable'}")
        else:
            skipped.append(f"{path.name} (not .json, .abc, .md or .txt)")

    def fill(new, current):
        """New text wins unless the box is occupied and overwrite is off."""
        if new is None:
            return gr.update(), False
        if not overwrite and (current or "").strip():
            return gr.update(), True
        return gr.update(value=new), False

    abc_u, abc_kept = fill(new_abc, current_abc)
    style_u, style_kept = fill(new_style, current_style)
    lyrics_u, lyrics_kept = fill(new_lyrics, current_lyrics)

    title_u = gr.update()
    if title_from and (overwrite or not (current_title or "").strip()):
        title_u = gr.update(value=sanitize_song_id(title_from))

    param_u = [gr.update(value=new_params[k]) if k in new_params else gr.update()
               for k in PARAM_KEYS]

    panel_u = gr.update(open=True) if new_abc is not None else gr.update()

    msg = []
    if read:
        msg.append("Loaded " + ", ".join(read) + ".")
    kept = [n for n, k in (("style prompt", style_kept), ("lyrics", lyrics_kept),
                           ("score", abc_kept)) if k]
    if kept:
        joined = kept[0] if len(kept) == 1 else ", ".join(kept[:-1]) + " and " + kept[-1]
        msg.append(f"Kept the existing {joined} — tick **replace** and drop again "
                   f"to overwrite.")
    if new_params:
        msg.append("Set " + ", ".join(
            f"{PARAM_LABELS.get(k, k)} {new_params[k]}" for k in PARAM_KEYS
            if k in new_params) + ".")
    if skipped:
        msg.append("Skipped " + ", ".join(skipped) + ".")

    return tuple([title_u, style_u, lyrics_u, abc_u, panel_u, " ".join(msg)] + param_u)


# ==================== PERSISTENT & FACTORY PRESETS ====================
PRESETS_FILE = Path("presets.json")

# The two generation stages want very different repetition penalties: the codec
# stage suppresses looping, the symbolic stage must be free to restate a theme.
SCORE_REP_PEN_DEFAULT = 1.005
SEM_REP_PEN_DEFAULT = 1.18

# Every tunable, in one canonical order. The UI builds its components from this,
# presets serialise from it, and every wiring list is assembled from it, so a new
# parameter is added in exactly one place.
#   (key, default, type)
PARAM_SPEC = [
    ("score_temp",     0.75,  float),
    ("score_top_p",    0.90,  float),
    ("score_rep_pen",  1.005, float),
    ("score_top_k",    30,    int),
    ("score_pen_win",  100,   int),
    ("sem_temp",       1.15,  float),
    ("sem_top_p",      0.95,  float),
    ("rep_pen",        1.18,  float),
    ("sem_top_k",      100,   int),
    ("sem_pen_win",    50,    int),
    ("sem_min_tokens", 200,   int),
    ("sem_max_tokens", 9000,  int),
    ("cfg_scale",      1.0,   float),
    ("flow_steps",     12,    int),
    ("seed",           404,   int),
    ("cot_mode",       "full", str),
]
PARAM_KEYS = [k for k, _, _ in PARAM_SPEC]
PARAM_DEFAULTS = {k: d for k, d, _ in PARAM_SPEC}
PARAM_CASTS = {k: t for k, _, t in PARAM_SPEC}


def param_value(preset, key):
    """One parameter out of a stored preset, with the default for keys saved before it existed."""
    default = PARAM_DEFAULTS[key]
    caster = dict((k, t) for k, _, t in PARAM_SPEC)[key]
    try:
        return caster(preset.get(key, default))
    except (TypeError, ValueError):
        return default


def preset_params(preset):
    """Every parameter of a stored preset, in canonical order."""
    return [param_value(preset, k) for k in PARAM_KEYS]

FACTORY_PRESETS = {
    "Progressive Cyber-Hopkins Suite": {
        "style": "English, progressive hybrid epic with drastic section transitions: begins with fragile spectral ambient choral drone, builds into angular math rock clean indie guitars, erupts into heavy lofi trap beat drop with gushing 808 sub-bass and rolling ricochet hi-hats, suddenly subsides into soft fragile tape-decay ambient breakdown, then crescendos into an explosive post-rock oceanic shimmer wall with soaring tremolo guitars and massive drums, dynamic whispered to soaring duet vocals, 134 BPM",
        "lyrics": "[Intro - Spectral Ambient, Fragile Choral Whispers, Zero Percussion]\nDappled pings in deep fiber-folds\nCold-kernel... cached, strange\nFolded in unmapped range\n\n[Verse 1 - Math Post-Alt Indie, Clean Angular Guitars, Hesitant Slow Build]\nMorning’s admin, flash-cache wing\n(Voice 1) Brindled baud-rate, binary-bright\n(Voice 2) Bus-line tangled in dead-link night\nInstress, bit-sire, spit-myre\nBrimming over, slow collapse...\n\n[Pre-Drop - Rhythmic Tension, Ricochet Hat Accelerando]\nSkittering eight-oh-eight in a muffled ring\nCount the syncopated teeth...\nBrace the buffer spill!\n\n[Chorus - Explosive Lofi Trap Drop, Gushing 808 Ricochets, Heavy Sub-Bass]\nInscape lattice, velvet numb!\nRolling touch as the traces come!\n(Voice 1) Lossless, hyper-threaded\n(Voice 2) Bleeding out bandwidth sky\n(Duet) Sub-bass plunges raw-wound wire!\nParity permeable, phosphor glow\nFold the current down below!\n\n[Bridge - Sudden Subside to Ambient Spectral, Tape Decay, Soft Fragile Drone]\nBleed out the beat.\nBreathe in the bus.\nCold architecture murmuring hush.\nFragmented, fault-tolerant, frail...\n(Whispered close-mic)\nRock-racked router, sprung and slight\nGlitch-cluster clicking in soft backlight.\n\n[Climax - Massive Post-Rock Oceanic Shimmer Wall, Tremolo Reverb Crescendo, Soaring Drums]\nHigh-gain, heat-sink, off-beat caress!\nChecksum suspended in supple flesh!\nNot a frame dropped, caught in the crawl!\nDappled data on dendrite wall!\n(Soaring duet & oceanic guitar swell)\nEigen-node! Flash-cache wing!\nLet the infinite shimmer sing!\n\n[Outro - Slow Ambient Dissolve, Distant Choral Hiss]\nTrace in the latent space...\nDissolve into light.",
        "custom_title": "progressive_cyber_hopkins",
        "score_temp": 0.75,
        "score_top_p": 0.9,
        "sem_temp": 1.15,
        "sem_top_p": 0.95,
        "rep_pen": 1.18,
        "cfg_scale": 1.0,
        "seed": 404,
        "flow_steps": 16
    },
    "Cybernetic Sprung-Hopkins Lofi Trap": {
        "style": "English, intimate lofi trap merger, syncopated 808 sub-bass ricochets, tape flutter, imperfect choral and duet whispers, cybernetic sprung-rhythm cadence, 128 BPM",
        "lyrics": "[Intro - Intimate Whispered Choral, Tape Flutter, Sub-Bass Hum]\n(Voice 1) Dappled pings in deep fiber-folds...\n(Voice 2) Cold-kernel cached, unmapped range.\n\n[Verse 1 - Sprung Cadence, Syncopated Close-Mic Whispers, Angular Guitars]\nMorning’s admin, flash-cache wing,\nBrindled baud-rate, binary-bright,\nBus-line tangled in dead-link night,\nInstress, bit-sire, spit-myre,\nBrimming over, slow collapse...\n\n[Chorus - Heavy Lofi 808 Drop, Rolling Ricochet Hats, Duet Vocals]\nInscape lattice, velvet numb!\nRolling touch as the traces come!\nSub-bass plunges raw-wound wire,\nParity permeable, phosphor glow,\nFold the current down below!\n\n[Bridge - Sudden Tape-Decay Breakdown, Fragile Drone]\nBleed out the beat.\nBreathe in the bus.\nRock-racked router, sprung and slight,\nGlitch-cluster clicking in soft backlight.\n\n[Outro - Syncopated Ricochet Fade]\nNot a frame dropped...\nCaught in the crawl.",
        "custom_title": "cyber_hopkins_trap",
        "score_temp": 0.8,
        "score_top_p": 0.9,
        "sem_temp": 1.12,
        "sem_top_p": 0.95,
        "rep_pen": 1.15,
        "cfg_scale": 1.0,
        "seed": 777,
        "flow_steps": 16
    },
    "Quirky Lofi Math Shoegaze": {
        "style": "English, lo-fi indie math rock, fuzzy shoegaze guitars, deadpan vocal fry, imperfect close-mic slacker delivery, angular clean chime riffs, hazy tape saturation, cassette hiss, dynamic swells, unpolished, 98 BPM",
        "lyrics": "[Verse 1]\nUnphased abrupt interim truncated mood\nFit the glade glandular esoteric zoo\nEtching it, itching e, infinite finicky\nSpilling cold algorithms in a lukewarm \nStatic wire humming blue\nfragments as the tape bleeds through\n\n[Chorus]\nEtching it, itching e, count the syncopated teeth\nDrifting undertones tangled in the bedroom blur\nResolves the way you thought we were\n\n[Verse 2]\nAngular cadence in an off-beat crawl\nCassette flutter bouncing off unpainted wall\nA twitch of a knee, a flicker of lime\nSplitting seconds to justify time\nGlade glandular shadows cross the core\nLet the vintage chorus pulse in the pore\n\n[Chorus]\nPermeable permeable permeable\nDrifting drifting drifting\n\n[Bridge]\nAbrupt.\nInterim.\nTruncated spin.\nFrequencies collapse while feedback rushes in\n\n[Outro]\nPermeable permeable permeable\nDrifting drifting drifting",
        "custom_title": "quirky_math_shoegaze",
        "score_temp": 0.85,
        "score_top_p": 0.92,
        "sem_temp": 1.1,
        "sem_top_p": 0.95,
        "rep_pen": 1.25,
        "cfg_scale": 1.0,
        "seed": 108,
        "flow_steps": 16
    },
    "Warm Acoustic Folk & Piano": {
        "style": "English, warm piano pop, expressive female voice, acoustic piano, rounded bass and light drums, lyrical memorable melody, unhurried phrasing, 88 BPM",
        "lyrics": "[Verse]\nNeon fades along the lane\nFootsteps keep the time of rain\nFold the night and leave it here\nMorning has a sky to clear\n\n[Chorus]\nLet the day come into view\nEvery road begins with you\nHold a little room for light\nWe will sing beyond the night",
        "custom_title": "warm_acoustic_folk",
        "score_temp": 0.65,
        "score_top_p": 0.85,
        "sem_temp": 0.95,
        "sem_top_p": 0.92,
        "rep_pen": 1.1,
        "cfg_scale": 1.0,
        "seed": 42,
        "flow_steps": 16
    }
}

_DELETE_CONFIRM_TARGET = None


# ==================== SOUNDS AND SONGS ====================
# A preset used to hold two unrelated things: a sound (the 16 parameters) and a
# song (title, prompt, lyrics, score). Saving a lyric therefore re-saved the
# parameters, every piece became a preset, and the list grew without bound.
# They are now two lists, chosen independently, so any sound can play any song —
# and a rating can be attributed to one or the other instead of to a bundle in
# which everything varied at once.

SOUNDS_FILE = Path("sounds.json")
SONGS_FILE = Path("songs.json")

SONG_KEYS = ["custom_title", "style", "lyrics", "abc"]

FACTORY_SOUNDS = {"Studio default": dict(PARAM_DEFAULTS)}
FACTORY_SONGS = {
    name: {k: v for k, v in entry.items() if k in SONG_KEYS}
    for name, entry in FACTORY_PRESETS.items()
}


def _read_json_dict(path):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sound_signature(entry):
    """Two sounds are the same sound when all 16 values agree."""
    out = []
    for key, default, caster in PARAM_SPEC:
        try:
            v = caster(entry.get(key, default))
        except (TypeError, ValueError):
            v = default
        out.append(round(v, 4) if caster is float else v)
    return tuple(out)


def migrate_presets():
    """
    Split presets.json into sounds.json and songs.json, once.

    presets.json is left on disk untouched. Sounds that are identical in all 16
    values collapse into one, named after the first preset that used it — which
    is the point of the split: many pieces, few sounds.
    """
    if SOUNDS_FILE.exists() or SONGS_FILE.exists():
        return None
    stored = _read_json_dict(PRESETS_FILE)
    if not stored:
        return None

    sounds, songs, by_sig = {}, {}, {}
    for name, entry in stored.items():
        if not isinstance(entry, dict):
            continue
        sig = _sound_signature(entry)
        if sig in by_sig:
            sound_name = by_sig[sig]
        else:
            sound_name = name
            by_sig[sig] = sound_name
            sounds[sound_name] = {k: param_value(entry, k) for k in PARAM_KEYS}
        song = {k: (entry.get(k) or "").strip() for k in SONG_KEYS if entry.get(k)}
        if song:
            song["sound"] = sound_name     # what it was rendered with, as a default
            songs[name] = song

    SOUNDS_FILE.write_text(json.dumps(sounds, indent=2, ensure_ascii=False), encoding="utf-8")
    SONGS_FILE.write_text(json.dumps(songs, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(stored), len(sounds), len(songs)


def load_sounds():
    out = dict(FACTORY_SOUNDS)
    out.update(_read_json_dict(SOUNDS_FILE))
    return out


def load_songs():
    out = dict(FACTORY_SONGS)
    out.update(_read_json_dict(SONGS_FILE))
    return out


def save_sounds_to_disk(d):
    SOUNDS_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def save_songs_to_disk(d):
    SONGS_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------- sounds

def select_sound(name):
    """Load the 16 values. A name not on the list leaves them alone."""
    global _DELETE_CONFIRM_TARGET
    _DELETE_CONFIRM_TARGET = None
    clean = (name or "").strip()
    sounds = load_sounds()
    if clean in sounds:
        return tuple(preset_params(sounds[clean]) + [f"Loaded sound **{clean}**.", clean])
    status = f"New sound **{clean}** — Save to create it." if clean else ""
    return tuple([gr.update() for _ in PARAM_SPEC] + [status, clean])


def save_sound(name, *values):
    clean = (name or "").strip()
    if not clean:
        return gr.update(), "Name the sound first.", gr.update(), gr.update()
    if clean in FACTORY_SOUNDS:
        clean = f"{clean} (my take)"
    entry = {}
    for (key, default, caster), value in zip(PARAM_SPEC, values):
        try:
            entry[key] = caster(value)
        except (TypeError, ValueError):
            entry[key] = default
    entry["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stored = _read_json_dict(SOUNDS_FILE)
    stored[clean] = entry
    save_sounds_to_disk(stored)
    return (gr.update(choices=list(load_sounds().keys()), value=clean),
            f"Saved sound **{clean}**.", clean,
            sound_button_state(clean, *values))


def revert_sound(name):
    clean = (name or "").strip()
    width = len(PARAM_SPEC)
    if clean not in load_sounds():
        return tuple([gr.update()] * width + [f"**{clean}** was never saved.", clean])
    res = list(select_sound(clean))
    res[width] = f"Reverted sound **{clean}**."
    return tuple(res)


def sound_button_state(name, *values):
    clean = (name or "").strip()
    if not clean:
        return gr.update(value="Name it", variant="secondary", interactive=False)
    sounds = load_sounds()
    if clean not in sounds:
        return gr.update(value="Save as new", variant="primary", interactive=True)
    same = _sound_signature(sounds[clean]) == _sound_signature(
        dict(zip(PARAM_KEYS, values))
    )
    if same:
        return gr.update(value="Saved", variant="secondary", interactive=False)
    if clean in FACTORY_SOUNDS:
        return gr.update(value="Save as copy", variant="primary", interactive=True)
    return gr.update(value="Save changes", variant="primary", interactive=True)


# ----------------------------------------------------------------------- songs

def select_song(name):
    """Load title, prompt, lyrics and score. A song may name the sound it came with."""
    global _DELETE_CONFIRM_TARGET
    _DELETE_CONFIRM_TARGET = None
    clean = (name or "").strip()
    songs = load_songs()
    if clean in songs:
        s = songs[clean]
        title = (s.get("custom_title") or _slugify_title(clean)).strip()
        return (gr.update(value=s.get("style", "")),
                gr.update(value=s.get("lyrics", "")),
                gr.update(value=title),
                gr.update(value=s.get("abc", "")),
                f"Loaded song **{clean}**.", clean)
    status = f"New song **{clean}** — Save to create it." if clean else ""
    title_u = gr.update(value=_slugify_title(clean)) if clean else gr.update()
    return (gr.update(), gr.update(), title_u, gr.update(), status, clean)


def save_song(name, custom_title, style, lyrics, abc):
    clean = (name or "").strip()
    if not clean:
        return gr.update(), "Name the song first.", gr.update(), gr.update()
    if clean in FACTORY_SONGS:
        clean = f"{clean} (my take)"
    entry = {
        "custom_title": (custom_title or "").strip(),
        "style": (style or "").strip(),
        "lyrics": (lyrics or "").strip(),
        "abc": (abc or "").strip(),
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    stored = _read_json_dict(SONGS_FILE)
    stored[clean] = entry
    save_songs_to_disk(stored)
    return (gr.update(choices=list(load_songs().keys()), value=clean),
            f"Saved song **{clean}**.", clean,
            song_button_state(clean, custom_title, style, lyrics, abc))


def revert_song(name):
    clean = (name or "").strip()
    if clean not in load_songs():
        return (gr.update(), gr.update(), gr.update(), gr.update(),
                f"**{clean}** was never saved.", clean)
    res = list(select_song(clean))
    res[4] = f"Reverted song **{clean}**."
    return tuple(res)


def _song_signature(entry):
    return tuple((entry.get(k) or "").strip() for k in SONG_KEYS)


def song_button_state(name, custom_title, style, lyrics, abc):
    clean = (name or "").strip()
    if not clean:
        return gr.update(value="Name it", variant="secondary", interactive=False)
    songs = load_songs()
    live = {"custom_title": custom_title, "style": style, "lyrics": lyrics, "abc": abc}
    if clean not in songs:
        return gr.update(value="Save as new", variant="primary", interactive=True)
    if _song_signature(songs[clean]) == _song_signature(live):
        return gr.update(value="Saved", variant="secondary", interactive=False)
    if clean in FACTORY_SONGS:
        return gr.update(value="Save as copy", variant="primary", interactive=True)
    return gr.update(value="Save changes", variant="primary", interactive=True)


# ------------------------------------------------------------------- deleting

def _delete_from(path, factory, name, kind):
    """Two presses to delete, and factory entries never."""
    global _DELETE_CONFIRM_TARGET
    clean = (name or "").strip()
    token = f"{kind}:{clean}"
    if not clean:
        return gr.update(), f"No {kind} selected."
    if clean in factory:
        _DELETE_CONFIRM_TARGET = None
        return gr.update(), f"**{clean}** is a factory {kind} and cannot be deleted."
    stored = _read_json_dict(path)
    if clean not in stored:
        _DELETE_CONFIRM_TARGET = None
        return gr.update(), f"No {kind} named **{clean}**."
    if _DELETE_CONFIRM_TARGET != token:
        _DELETE_CONFIRM_TARGET = token
        return gr.update(), (f"Delete the {kind} **{clean}**? Press again to confirm, "
                             f"or choose another to cancel.")
    _DELETE_CONFIRM_TARGET = None
    del stored[clean]
    path.write_text(json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8")
    remaining = dict(factory)
    remaining.update(stored)
    choices = list(remaining.keys())
    return (gr.update(choices=choices, value=choices[0] if choices else None),
            f"Deleted the {kind} **{clean}**.")


def delete_sound(name):
    return _delete_from(SOUNDS_FILE, FACTORY_SOUNDS, name, "sound")


def delete_song(name):
    return _delete_from(SONGS_FILE, FACTORY_SONGS, name, "song")


def load_all_presets():
    """Load all presets, guaranteeing FACTORY_PRESETS are always present and protected."""
    presets = dict(FACTORY_PRESETS)
    if PRESETS_FILE.exists():
        try:
            data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    presets[k] = v
        except Exception:
            pass
    return presets


def save_presets_to_disk(presets):
    """Save full presets dictionary to presets.json."""
    PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")


def _slugify_title(name):
    """Filesystem-safe lowercase slug used to auto-derive a track title from a preset name."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _field_signature(style, lyrics, custom_title, *values):
    """Comparable snapshot of every value a preset stores."""
    normalised = []
    for (key, default, caster), value in zip(PARAM_SPEC, values):
        try:
            cast = caster(value)
        except (TypeError, ValueError):
            cast = default
        normalised.append(round(cast, 4) if caster is float else cast)
    return ((style or "").strip(), (lyrics or "").strip(), (custom_title or "").strip(),
            tuple(normalised))


def _stored_signature(preset):
    """Presets saved before a parameter existed fall back to that parameter's default."""
    return _field_signature(
        preset.get("style", ""), preset.get("lyrics", ""), preset.get("custom_title", ""),
        *preset_params(preset)
    )


def save_button_state(name, style, lyrics, custom_title, *values):
    """Save button reports whether the current settings already exist under this name."""
    clean = (name or "").strip()
    if not clean:
        return gr.update(value="Name this preset", variant="secondary", interactive=False)

    presets = load_all_presets()
    if clean not in presets:
        return gr.update(value="💾 Save as New", variant="primary", interactive=True)

    try:
        unchanged = _stored_signature(presets[clean]) == _field_signature(
            style, lyrics, custom_title, *values
        )
    except (TypeError, ValueError):
        unchanged = False

    if unchanged:
        return gr.update(value="✓ Saved", variant="secondary", interactive=False)
    if clean in FACTORY_PRESETS:
        return gr.update(value="💾 Save as Copy", variant="primary", interactive=True)
    return gr.update(value="💾 Save Changes", variant="primary", interactive=True)


def select_preset(name, current_title, last_name):
    """
    Single-dropdown behaviour:
      - an existing name loads that preset in full
      - a newly typed name leaves the settings alone and only re-derives the
        track title when the title was still inherited from the previous preset
    """
    global _DELETE_CONFIRM_TARGET
    _DELETE_CONFIRM_TARGET = None

    clean = (name or "").strip()
    presets = load_all_presets()

    if clean in presets:
        p = presets[clean]
        return tuple(
            [p.get("style", ""), p.get("lyrics", ""), p.get("custom_title", "")]
            + preset_params(p)
            + [f"✅ Loaded **{clean}**.", clean]
        )

    prev = presets.get((last_name or "").strip(), {})
    cur = (current_title or "").strip()
    inherited = (
        not cur
        or cur == (prev.get("custom_title", "") or "").strip()
        or cur == _slugify_title(last_name)
    )
    title_update = gr.update(value=_slugify_title(clean)) if (clean and inherited) else gr.update()
    status = f"✏️ New preset **{clean}** — click Save to create it." if clean else ""

    return tuple(
        [gr.update(), gr.update(), title_update]
        + [gr.update() for _ in PARAM_SPEC]
        + [status, last_name]
    )


def revert_preset(name):
    """Restore the fields to whatever is stored on disk under this preset name."""
    clean = (name or "").strip()
    width = 3 + len(PARAM_SPEC)
    if clean not in load_all_presets():
        return tuple(
            [gr.update()] * width
            + [f"⚠️ **{clean}** has never been saved — nothing to revert to.", clean]
        )
    res = list(select_preset(clean, "", clean))
    res[width] = f"↺ Reverted **{clean}** to its saved state."
    return tuple(res)


def save_preset(name, style, lyrics, custom_title, *values):
    """Create or overwrite a preset with every current field."""
    clean_name = (name or "").strip()
    if not clean_name:
        return gr.update(), "⚠️ Please name the preset first.", gr.update(), gr.update()

    # Factory templates stay pristine: saving over one forks a copy instead.
    if clean_name in FACTORY_PRESETS:
        clean_name = f"{clean_name} (My Take)"

    entry = {
        "style": (style or "").strip(),
        "lyrics": (lyrics or "").strip(),
        "custom_title": (custom_title or "").strip(),
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for (key, default, caster), value in zip(PARAM_SPEC, values):
        try:
            entry[key] = caster(value)
        except (TypeError, ValueError):
            entry[key] = default

    presets = load_all_presets()
    presets[clean_name] = entry
    save_presets_to_disk(presets)

    btn = save_button_state(clean_name, style, lyrics, custom_title, *values)
    return (
        gr.update(choices=list(presets.keys()), value=clean_name),
        f"💾 Saved **{clean_name}**.",
        clean_name,
        btn
    )


def delete_custom_preset(name):
    """Safely delete a custom preset with two-step confirmation and factory protection."""
    global _DELETE_CONFIRM_TARGET
    clean_name = (name or "").strip()
    if not clean_name:
        return gr.update(), "⚠️ No preset selected."

    # 1. Protect Factory Presets
    if clean_name in FACTORY_PRESETS:
        _DELETE_CONFIRM_TARGET = None
        return gr.update(), f"🛡️ **Protected Base Preset**: '{clean_name}' is a permanent factory template and cannot be deleted."

    presets = load_all_presets()
    if clean_name not in presets:
        _DELETE_CONFIRM_TARGET = None
        return gr.update(), f"❌ Preset '{clean_name}' not found."

    # 2. Two-step confirmation for custom variations
    if _DELETE_CONFIRM_TARGET != clean_name:
        _DELETE_CONFIRM_TARGET = clean_name
        return gr.update(), (
            f"⚠️ **Confirm Deletion**: You are about to delete preset **'{clean_name}'**.\n"
            f"Click **🗑️ Delete** once more to permanently remove it (or select another preset to cancel)."
        )

    # Proceed with deletion on 2nd click
    _DELETE_CONFIRM_TARGET = None
    if PRESETS_FILE.exists():
        try:
            stored = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
            if clean_name in stored:
                del stored[clean_name]
                save_presets_to_disk(stored)
        except Exception:
            pass

    presets = load_all_presets()
    choices = list(presets.keys())
    fallback = choices[0] if choices else None
    return gr.update(choices=choices, value=fallback), f"🗑️ Permanently removed preset **'{clean_name}'**."


# ==================== LIVE PROGRESS & CANCELLATION ====================
# The pipeline emits every generated token through on_token and polls `cancelled`
# between steps. Without these the Gradio bar sits at one value for the whole
# render, which is indistinguishable from a hang.

_CANCEL = {"stop": False}


def request_cancel():
    """Ask the running generation to stop at its next token."""
    _CANCEL["stop"] = True
    return "🛑 Stopping at the next token..."


def _is_cancelled():
    return _CANCEL["stop"]


def make_progress_hooks(progress, start_frac, token_frac, expected_tokens, label,
                        audio_stage=False):
    """
    Two hooks driven by the pipeline itself.

    on_token   fires per generated token — real counts and rate.
    cancelled  is polled between tokens AND inside the ODE solver, so once the
               token stream goes quiet it doubles as the heartbeat for the audio
               synthesis stage, which emits no tokens.
    """
    _now = time.perf_counter()
    state = {"n": 0, "t0": _now, "last": _now, "last_token": _now, "polls": 0}

    def on_token(phase, token):
        state["n"] += 1
        now = time.perf_counter()
        state["last_token"] = now
        if now - state["last"] < 0.4:
            return
        state["last"] = now
        n, elapsed = state["n"], now - state["t0"]
        rate = n / elapsed if elapsed > 0 else 0.0
        frac = start_frac + (token_frac - start_frac) * (1.0 - math.exp(-n / float(expected_tokens)))
        progress(frac, desc=f"{label} — {n} tokens · {rate:.1f} tok/s · {int(elapsed)}s")

    def cancelled():
        if _CANCEL["stop"]:
            return True
        if not audio_stage:
            return False
        now = time.perf_counter()
        # Tokens have stopped arriving: the flow solver is running.
        if state["n"] > 0 and now - state["last_token"] > 2.0:
            state["polls"] += 1
            if now - state["last"] >= 0.5:
                state["last"] = now
                steps_done = state["polls"] // 2
                frac = token_frac + (0.97 - token_frac) * (1.0 - math.exp(-steps_done / 24.0))
                progress(frac, desc=f"🌊 Synthesizing audio — {steps_done} flow steps · "
                                    f"{int(now - state['t0'])}s total")
        return False

    return on_token, cancelled


def generate_plan_step(style, lyrics, seed, score_temp, score_top_p, score_rep_pen,
                       score_top_k=30, score_pen_win=100, cot_mode="full",
                       progress=gr.Progress()):
    _CANCEL["stop"] = False
    progress(0.02, desc="🎼 Loading model & planning score...")
    pipe = get_pipeline()
    score_temp = float(score_temp) if score_temp is not None else 0.75
    score_top_p = float(score_top_p) if score_top_p is not None else 0.90
    score_rep_pen = float(score_rep_pen) if score_rep_pen is not None else SCORE_REP_PEN_DEFAULT
    seed = int(seed) if seed is not None else 404
    cot_mode = (cot_mode or "full").strip() or "full"

    if cot_mode == "off":
        return "", "*Score mode is off — no ABC is generated; the model goes straight to audio.*", \
               "ℹ️ Score mode **off**: nothing to plan. Use Synthesize Audio."

    request = {
        "style": (style or "").strip(),
        "lyrics": (lyrics or "").strip(),
        "cot": cot_mode,
        "seed": seed,
        "abc_sampling": {
            "temperature": score_temp,
            "top_p": score_top_p,
            "repetition_penalty": score_rep_pen,
            "top_k": int(score_top_k),
            "penalty_window": int(score_pen_win),
        }
    }

    start = time.perf_counter()
    on_token, cancelled = make_progress_hooks(progress, 0.05, 0.95, 1400, "🎼 Planning score")
    try:
        plan = pipe.plan(**request, cancelled=cancelled, on_token=on_token)
    except InterruptedError:
        return "", "", "🛑 Planning cancelled."
    elapsed = time.perf_counter() - start

    abc = plan.abc or ""
    metrics = parse_abc_metrics(abc)
    mode_note = " · melody only" if cot_mode == "melody" else ""
    status_msg = f"✅ Plan generated in {elapsed:.1f}s ({len(plan.abc_ids)} tokens{mode_note})"

    return abc, metrics, status_msg


def extract_lyric_keywords(lyrics_text):
    """Extract key chorus words or opening lyrics for song identification."""
    if not lyrics_text or not lyrics_text.strip():
        return "Track"

    text = lyrics_text.strip()

    # 1. Look for [Chorus...] or [Hook...] or [Refrain...]
    chorus_match = re.search(r"\[(?:Chorus|Hook|Refrain)[^\]]*\]\s*([^\[]+)", text, re.IGNORECASE)
    candidate_lines = []
    if chorus_match:
        section_body = chorus_match.group(1).strip()
        candidate_lines = [line.strip() for line in section_body.splitlines() if line.strip()]

    # 2. If no chorus section found, look for first non-section line
    if not candidate_lines:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("["):
                candidate_lines.append(line)
                break

    if not candidate_lines:
        return "Track"

    # Take first line and remove parenthetical directions e.g. (Voice 1), (Duet), etc.
    first_line = candidate_lines[0]
    first_line = re.sub(r"\([^)]*\)", "", first_line)

    # Clean punctuation except letters, numbers, spaces
    words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", first_line)
    if not words:
        return "Track"

    # Pick first 2 to 4 prominent words (up to ~28 chars total)
    selected_words = []
    total_len = 0
    for w in words:
        if len(selected_words) >= 4 or (total_len + len(w) > 28 and len(selected_words) >= 2):
            break
        selected_words.append(w.capitalize() if w.islower() else w)
        total_len += len(w) + 1

    return " ".join(selected_words) if selected_words else "Track"


def sanitize_song_id(text):
    """Filesystem-safe song identifier: spaces and illegal characters become underscores."""
    cleaned = re.sub(r"[^\w.-]+", "_", (text or "").strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip("._")
    return cleaned[:80]


# Short codes for the folder-name delta tag, so A/B renders of one song are
# distinguishable at a glance in Finder without the old 120-character names.
PARAM_CODES = {
    "score_temp": "hx", "score_top_p": "stp", "score_rep_pen": "srp",
    "score_top_k": "stk", "score_pen_win": "sw",
    "sem_temp": "vw", "sem_top_p": "mtp", "rep_pen": "vrp",
    "sem_top_k": "mtk", "sem_pen_win": "mw",
    "sem_min_tokens": "min", "sem_max_tokens": "max",
    "cfg_scale": "cfg", "flow_steps": "fs", "seed": "sd", "cot_mode": "mode",
}


def _fmt_param(value):
    if isinstance(value, float):
        text = f"{value:g}"
        return text.lstrip("0") if text.startswith("0.") else text
    return str(value)


def build_param_delta_tag(preset_name, params, limit=4):
    """
    Compact tag naming only what differs from the preset's saved baseline, e.g.
    [vw1.35_stk80]. Renders of one song at different settings stay distinguishable
    without encoding all sixteen parameters.
    """
    if not params:
        return ""
    baseline = load_all_presets().get((preset_name or "").strip(), {})
    bits = []
    for key, default, caster in PARAM_SPEC:
        if key not in params:
            continue
        try:
            current = caster(params[key])
            base = caster(baseline.get(key, default))
        except (TypeError, ValueError):
            continue
        if isinstance(current, float):
            same = abs(current - base) < 1e-9
        else:
            same = current == base
        if not same:
            bits.append(f"{PARAM_CODES.get(key, key)}{_fmt_param(current)}")
    if not bits:
        return ""
    if len(bits) > limit:
        bits = bits[:limit] + ["etc"]
    return "[" + "_".join(bits) + "]"


# ==================== ARCHIVE FORMAT ====================
# The pipeline writes 24-bit FLAC (~11 MB per minute). latent.npy is ~0.4 MB per
# minute and the VAE decode is deterministic, so latents + MP3 is a complete
# archive: the lossless file can be regenerated exactly, in seconds, on demand.
AUDIO_FORMATS = {
    "mp3 320 + latents": ("mp3", "320k"),
    "mp3 192 + latents": ("mp3", "192k"),
    "flac + mp3": ("both", "320k"),
    "flac (lossless)": ("flac", None),
}
DEFAULT_AUDIO_FORMAT = "mp3 320 + latents"


def encode_mp3(flac_path, dest, bitrate="320k"):
    """FLAC -> MP3 via libsndfile (>=1.1 writes MP3), falling back to ffmpeg."""
    import shutil
    dest = Path(dest)
    try:
        import soundfile as sf
        if "MP3" in sf.available_formats():
            data, rate = sf.read(str(flac_path), always_2d=True)
            sf.write(str(dest), data, rate, format="MP3",
                     subtype="MPEG_LAYER_III" if "MPEG_LAYER_III" in sf.available_subtypes("MP3") else None)
            if dest.exists() and dest.stat().st_size > 1024:
                return True
        dest.unlink(missing_ok=True)
    except Exception:
        dest.unlink(missing_ok=True)

    if shutil.which("ffmpeg"):
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(flac_path),
                 "-codec:a", "libmp3lame", "-b:a", bitrate, str(dest)],
                check=True, timeout=900
            )
            return dest.exists() and dest.stat().st_size > 1024
        except Exception:
            dest.unlink(missing_ok=True)
    return False


def apply_audio_format(output_dir, folder_name, fmt_label):
    """
    Convert and prune the rendered audio to the chosen archive format.
    Returns (path_to_playable_audio, note).
    """
    mode, bitrate = AUDIO_FORMATS.get(fmt_label, AUDIO_FORMATS[DEFAULT_AUDIO_FORMAT])
    output_dir = Path(output_dir)
    default_flac = output_dir / "audio.flac"
    named_flac = output_dir / f"{folder_name}.flac"
    source = named_flac if named_flac.exists() else default_flac
    if not source.exists():
        return None, "no audio written"

    if mode == "flac":
        return str(source), "24-bit FLAC"

    mp3_path = output_dir / f"{folder_name}.mp3"
    if not encode_mp3(source, mp3_path, bitrate or "320k"):
        return str(source), "MP3 encoder unavailable — kept FLAC"

    if mode == "both":
        return str(mp3_path), f"MP3 {bitrate} + FLAC"

    # mp3 only: latent.npy still reconstructs the lossless master exactly.
    # The two names share one inode, so count the bytes once.
    freed, seen = 0, set()
    for f in (named_flac, default_flac):
        if f.exists():
            try:
                st = f.stat()
                if st.st_ino not in seen:
                    seen.add(st.st_ino)
                    freed += st.st_size
                f.unlink()
            except OSError:
                pass
    if (output_dir / "latent.npy").exists():
        note = f"MP3 {bitrate}; {freed / 1e6:.0f} MB of FLAC dropped (re-decodable from latents)"
    else:
        note = f"MP3 {bitrate}; {freed / 1e6:.0f} MB of FLAC dropped"
    return str(mp3_path), note


def upgrade_flow_steps(track_name, steps=32, progress=gr.Progress()):
    """
    Re-solve the flow stage at a higher step count using the semantic tokens
    already on disk. The autoregressive stage — the five-minute one — is skipped
    entirely: semantic.npy holds its output, and the plan files hold the prefix
    it was conditioned on. Preview at 8-12 steps, upgrade the keepers to 32.
    """
    if not track_name:
        return None, "⚠️ No track selected."

    track_dir = Path("outputs") / track_name
    tokens_file = track_dir / "semantic.npy"
    if not tokens_file.exists():
        return None, f"❌ `{track_name}` has no semantic.npy — nothing to re-solve from."
    if not (track_dir / "plan_manifest.json").exists():
        return None, f"❌ `{track_name}` has no saved plan; a full re-render is the only route."

    steps = int(steps)
    target = track_dir / f"{track_name}_{steps}steps.flac"
    if target.exists():
        return str(target), f"✅ `{target.name}` already present."

    _CANCEL["stop"] = False
    progress(0.02, desc=f"Loading plan & semantic tokens...")

    try:
        import numpy as np
        import soundfile as sf
        from yue2.pipeline import SymbolicPlan, SemanticResult

        pipe = get_pipeline()
        plan = SymbolicPlan.load(track_dir)
        tokens = np.load(tokens_file, allow_pickle=False).tolist()
        semantic = SemanticResult(plan, tokens, {}, False)

        previous = pipe.generation_config
        pipe.generation_config = dataclasses.replace(previous, ode_steps=steps)

        on_token, cancelled = make_progress_hooks(
            progress, 0.05, 0.05, 1, f"🌊 Re-solving at {steps} steps", audio_stage=True
        )
        start = time.perf_counter()
        try:
            latents = pipe.synthesize(semantic, cancelled=cancelled)
            progress(0.9, desc="Decoding audio...")
            audio = pipe.decode(latents)
        finally:
            pipe.generation_config = previous

        sf.write(str(target), audio, 48000, subtype="PCM_24")
        elapsed = time.perf_counter() - start
    except InterruptedError:
        return None, "🛑 Upgrade cancelled."
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return None, f"❌ Upgrade failed: {exc}"

    meta = read_track_metadata(track_dir)
    if meta:
        meta.setdefault("upgrades", []).append(
            {"flow_steps": steps, "file": target.name,
             "at": datetime.datetime.now().isoformat(timespec="seconds")}
        )
        try:
            (track_dir / "track.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    size = target.stat().st_size / 1e6
    return str(target), (f"⬆ Re-solved at {steps} steps in {elapsed:.0f}s — `{target.name}` "
                         f"({size:.0f} MB). Generation was not re-run.")


def redecode_lossless(track_name):
    """Rebuild the 24-bit FLAC from latent.npy — VAE decode only, no regeneration."""
    if not track_name:
        return None, "⚠️ No track selected."
    track_dir = Path("outputs") / track_name
    latents_file = track_dir / "latent.npy"
    if not latents_file.exists():
        return None, f"❌ `{track_name}` has no latent.npy to decode from."

    target = track_dir / f"{track_name}.flac"
    if target.exists():
        return str(target), f"✅ `{target.name}` already present."

    try:
        import numpy as np
        import soundfile as sf
        pipe = get_pipeline()
        latents = np.load(latents_file, allow_pickle=False)
        start = time.perf_counter()
        audio = pipe.decode(latents)
        sf.write(str(target), audio, 48000, subtype="PCM_24")
        elapsed = time.perf_counter() - start
    except Exception as exc:
        return None, f"❌ Decode failed: {exc}"

    size = target.stat().st_size / 1e6
    return str(target), f"🎧 Rebuilt `{target.name}` ({size:.0f} MB, 24-bit) in {elapsed:.1f}s."


def build_track_folder_name(custom_title, lyrics_text, when=None, tag=""):
    """
    Sortable folder name: YYYY-MM-DD_HHMMSS_song_name[delta tag]
    Full parameters still travel with the track in track.json.
    """
    when = when or datetime.datetime.now()
    stamp = when.strftime("%Y-%m-%d_%H%M%S")
    song_id = sanitize_song_id(custom_title) or sanitize_song_id(extract_lyric_keywords(lyrics_text)) or "YuE_Track"
    suffix = f"_{tag}" if tag else ""
    return f"{stamp}_{song_id}{suffix}"


PARAM_LABELS = {
    "score_temp": "Harmonic Exploration",
    "score_top_p": "Score Top-P",
    "score_rep_pen": "Score Repetition",
    "score_top_k": "Score Top-K",
    "score_pen_win": "Score Penalty Window",
    "sem_temp": "Vocal Wildness",
    "sem_top_p": "Semantic Top-P",
    "rep_pen": "Vocal Repetition",
    "sem_top_k": "Semantic Top-K",
    "sem_pen_win": "Vocal Penalty Window",
    "sem_min_tokens": "Min Length",
    "sem_max_tokens": "Max Length",
    "cfg_scale": "CFG",
    "flow_steps": "Flow Steps",
    "seed": "Seed",
    "cot_mode": "Score Mode",
}


def write_track_metadata(output_dir, folder_name, track_title, preset_name,
                         style, lyrics, params, audio_path=None,
                         elapsed=None, audio_seconds=None, favorite=False,
                         song_name=""):
    """
    Write track.json beside the audio and, when mutagen is available, stamp the
    same values into the FLAC's Vorbis comments so the file carries them alone.
    `params` is a dict keyed by PARAM_KEYS.
    """
    clean = {}
    for key, default, caster in PARAM_SPEC:
        try:
            clean[key] = caster(params.get(key, default))
        except (TypeError, ValueError):
            clean[key] = default

    # `sound` and `song` are what the ratings are attributed to. `preset` stays,
    # holding both, so every track written before the split still reads.
    sound = (preset_name or "").strip()
    song = (song_name or "").strip()
    meta = {
        "folder": folder_name,
        "track_title": (track_title or "").strip(),
        "sound": sound,
        "song": song,
        "preset": " / ".join([p for p in (sound, song) if p]) or sound,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "favorite": bool(favorite),
        "style": (style or "").strip(),
        "lyrics": (lyrics or "").strip(),
        "parameters": clean,
    }
    if elapsed is not None:
        meta["render_seconds"] = round(float(elapsed), 1)
    if audio_seconds is not None:
        meta["audio_seconds"] = round(float(audio_seconds), 1)

    try:
        (Path(output_dir) / "track.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass

    if audio_path:
        try:
            from mutagen.flac import FLAC
            tags = FLAC(str(audio_path))
            tags["title"] = meta["track_title"] or folder_name
            tags["album"] = meta["preset"] or "YuE2"
            tags["date"] = meta["created"][:10]
            tags["comment"] = json.dumps(meta["parameters"], ensure_ascii=False)
            tags["description"] = meta["style"]
            tags.save()
        except Exception:
            pass

    return meta


def read_track_metadata(track_dir):
    """track.json for a render, or {} when it predates the sidecar."""
    meta_file = Path(track_dir) / "track.json"
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_display_name(track_name, display_name):
    """A human title for the playlist page, independent of the folder name."""
    if not track_name:
        return "⚠️ No track selected."
    track_dir = Path("outputs") / track_name
    if not track_dir.is_dir():
        return f"❌ Track '{track_name}' not found."
    meta = read_track_metadata(track_dir) or {"folder": track_name, "parameters": {}}
    clean = (display_name or "").strip()
    if clean:
        meta["display_name"] = clean
    else:
        meta.pop("display_name", None)
    try:
        (track_dir / "track.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        return f"❌ Could not write track.json: {exc}"
    return f"🏷️ `{track_name}` → **{clean}**" if clean else f"🏷️ Cleared the display name."


def load_display_name(track_name):
    if not track_name:
        return ""
    return read_track_metadata(Path("outputs") / track_name).get("display_name", "")


def public_title(meta, folder_name):
    """What a listener should see: display name, else track title, else the folder."""
    return (meta.get("display_name") or meta.get("track_title") or folder_name).strip()


def public_slug(title, taken):
    """URL-safe, collision-free file stem for the served folder."""
    base = re.sub(r"[^a-z0-9]+", "-", (title or "track").lower()).strip("-")[:60] or "track"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    taken.add(slug)
    return slug


def save_track_notes(track_name, notes):
    """Accompanying text for a track, stored in its track.json for the playlist page."""
    if not track_name:
        return "⚠️ No track selected."
    track_dir = Path("outputs") / track_name
    if not track_dir.is_dir():
        return f"❌ Track '{track_name}' not found."
    meta = read_track_metadata(track_dir) or {"folder": track_name, "parameters": {}}
    meta["notes"] = (notes or "").strip()
    try:
        (track_dir / "track.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        return f"❌ Could not write track.json: {exc}"
    return f"📝 Saved notes for `{track_name}`."


def load_track_notes(track_name):
    if not track_name:
        return ""
    return read_track_metadata(Path("outputs") / track_name).get("notes", "")


# app.py is a symlink in the working folder, so .resolve() lands in the
# repository it points into — which is where the published page belongs,
# ready to commit. Without a symlink this is root_dir and nothing changes.
REPO_DIR = Path(__file__).resolve().parent
PLAYLIST_DIR = REPO_DIR / "docs"


def _to_mp3(flac_path, dest, bitrate="320k"):
    """
    FLAC -> MP3 for the web page. libsndfile >= 1.1 writes MP3 directly; ffmpeg is
    the fallback. If neither works the FLAC is copied and linked as-is (Safari,
    Chrome and Firefox all decode FLAC, the files are simply large).
    """
    import shutil
    dest, source = Path(dest), Path(flac_path)
    if source.suffix.lower() == ".mp3":
        shutil.copy2(source, dest)
        return dest.name, "mp3"
    if encode_mp3(source, dest, bitrate):
        return dest.name, "mp3"
    fallback = dest.with_suffix(".flac")
    shutil.copy2(source, fallback)
    return fallback.name, "flac"


PLAYLIST_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    color-scheme: light dark;
    --ink: #18181b;
    --muted: #71717a;
    --line: #e4e4e7;
    --bg: #fbfbfc;
    --accent: #7c3aed;
    --card-bg: rgba(124, 58, 237, 0.035);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --ink: #ededf0;
      --muted: #a1a1aa;
      --line: #2a2a31;
      --bg: #151518;
      --accent: #a78bfa;
      --card-bg: rgba(167, 139, 250, 0.05);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font: 15px/1.65 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  }
  .wrap { max-width: 760px; margin: 0 auto; padding: 40px 20px 96px; }

  .hero-img {
    width: 100%;
    height: auto;
    border-radius: 12px;
    border: 1px solid var(--line);
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
    display: block;
    margin: 0 0 32px;
  }

  h1 { font-size: 1.85rem; font-weight: 750; margin: 0 0 6px; letter-spacing: -0.02em; }
  .sub { color: var(--muted); font-size: 0.9rem; margin: 0 0 32px; }

  .intro { margin: 0 0 44px; }
  .intro p { margin: 0 0 16px; line-height: 1.75; font-size: 1.02rem; }
  .intro h2 { font-size: 1.3rem; font-weight: 700; margin: 36px 0 14px; letter-spacing: -0.01em; color: var(--ink); }
  .intro h3 { font-size: 1.1rem; font-weight: 650; margin: 26px 0 10px; color: var(--ink); }
  .intro blockquote {
    margin: 22px 0;
    padding: 14px 18px;
    border-left: 3px solid var(--accent);
    background: var(--card-bg);
    border-radius: 0 8px 8px 0;
    color: var(--ink);
    font-size: 0.94rem;
    line-height: 1.65;
  }
  .intro blockquote p { margin: 0 0 8px; font-size: 0.94rem; }
  .intro blockquote p:last-child { margin-bottom: 0; }
  .intro blockquote cite { display: block; margin-top: 10px; font-size: 0.8rem; color: var(--muted); font-style: normal; font-weight: 600; }
  .intro ul { margin: 12px 0 20px 20px; padding: 0; }
  .intro li { margin-bottom: 10px; line-height: 1.65; font-size: 0.98rem; }
  .intro a { color: var(--accent); text-decoration: none; font-weight: 500; }
  .intro a:hover { text-decoration: underline; }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin: 24px 0 28px;
  }
  @media (max-width: 680px) {
    .stat-grid { grid-template-columns: 1fr; }
  }
  .stat-card {
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 14px 16px;
    background: var(--card-bg);
  }
  .stat-card .num {
    font-size: 1.18rem;
    font-weight: 700;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
  }
  .stat-card .num a {
    color: var(--accent);
    text-decoration: none;
  }
  .stat-card .num a:hover {
    text-decoration: underline;
  }
  .stat-card .lbl {
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 4px;
    line-height: 1.4;
  }
  .stat-card .lbl a {
    color: inherit;
    text-decoration: none;
  }
  .stat-card .lbl a:hover {
    color: var(--accent);
    text-decoration: underline;
  }

  .playlist-divider {
    border-top: 2px solid var(--line);
    margin: 48px 0 28px;
    padding-top: 24px;
  }
  .playlist-divider h2 {
    font-size: 1.4rem;
    font-weight: 750;
    margin: 0 0 6px;
    letter-spacing: -0.01em;
  }
  .playlist-divider p {
    color: var(--muted);
    font-size: 0.88rem;
    margin: 0;
  }

  .track { border-top: 1px solid var(--line); padding: 26px 0; }
  .track h2 { font-size: 1.08rem; font-weight: 650; margin: 0 0 2px; }
  .meta { color: var(--muted); font-size: 0.8rem; margin: 0 0 12px; }
  .meta span + span::before { content: " · "; }

  /* Refined MP3 Player Styling */
  audio {
    width: 100%;
    height: 38px;
    margin: 8px 0 14px;
    border-radius: 20px;
    accent-color: var(--accent);
    background: var(--card-bg);
    border: 1px solid var(--line);
    outline: none;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.03);
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }
  audio:hover, audio:focus {
    border-color: var(--accent);
    box-shadow: 0 2px 10px rgba(124, 58, 237, 0.12);
  }
  audio::-webkit-media-controls-panel,
  audio::-webkit-media-controls-enclosure {
    background: transparent;
  }
  .note { margin: 10px 0 0; }
  .note:empty { display: none; }
  details { margin-top: 12px; border: 1px solid var(--line); border-radius: 8px;
            padding: 0 12px; background: rgba(124,58,237,0.035); }
  details[open] { padding-bottom: 12px; }
  summary { cursor: pointer; padding: 9px 0; font-size: 0.82rem; color: var(--accent);
            font-weight: 600; list-style: none; }
  summary::-webkit-details-marker { display: none; }
  summary::before { content: "▸ "; }
  details[open] summary::before { content: "▾ "; }
  table { border-collapse: collapse; width: 100%; font-size: 0.79rem; }
  td { padding: 3px 0; vertical-align: top; }
  td:first-child { color: var(--muted); width: 52%; padding-right: 12px; }
  td:last-child { font-variant-numeric: tabular-nums; }
  .prompt { font-size: 0.79rem; color: var(--muted); margin: 10px 0 0;
            white-space: pre-wrap; }
  footer { border-top: 1px solid var(--line); margin-top: 40px; padding-top: 18px;
           color: var(--muted); font-size: 0.76rem; }
</style>
</head>
<body>
<div class="wrap">

<img class="hero-img" src="img/suno-shit-header.jpg" alt="Suno v.6 is a pile of shit — that's good news for experimental music" width="1920" height="1080">

<h1>__TITLE__</h1>
<p class="sub">__SUBTITLE__</p>

<!-- ===== EDIT ME: introduction ===== -->
<div class="intro">
__INTRO__
</div>
<!-- ===== /EDIT ME ===== -->

__TRACKS__

<footer>__FOOTER__</footer>
</div>
</body>
</html>
"""


def build_favorites_page(title="Favorites", subtitle="", convert_mp3=True):
    """
    Write a self-contained, servable folder:

        favorites_page/
          index.html          data inlined, so it works from file:// and from a server
          index.json          the whole playlist
          audio/<slug>.mp3    named from the display name, not the render folder
          data/<slug>.json    one file per track, full parameters and prompt

    Nothing in the served folder carries a 120-character render name.
    """
    favs = [t for t in get_track_data()
            if t.get("rating") is not None and t["rating"] >= FAVORITE_THRESHOLD]
    favs.sort(key=lambda t: -(t.get("rating") or 0))
    if not favs:
        return f"⚠️ Nothing rated {FAVORITE_THRESHOLD}+ yet.", None

    audio_dir = PLAYLIST_DIR / "audio"
    data_dir = PLAYLIST_DIR / "data"
    audio_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    page_path = PLAYLIST_DIR / "index.html"
    intro = ("<p>Renders kept from the YuE2 sessions. Open a track's parameters to see "
             "what produced it.</p>")
    if page_path.exists():
        try:
            previous = page_path.read_text(encoding="utf-8")
            start = previous.index('<div class="intro">') + len('<div class="intro">')
            kept = previous[start:previous.index("<!-- ===== /EDIT ME ===== -->")]
            kept = kept.rsplit("</div>", 1)[0].strip()
            if kept:
                intro = kept
        except (ValueError, OSError):
            pass

    taken, entries, converted = set(), [], 0
    for track in favs:
        meta = read_track_metadata(Path(track["path"]))
        shown = public_title(meta, track["name"])
        slug = public_slug(shown, taken)

        audio_href = ""
        if track.get("audio"):
            target = audio_dir / f"{slug}.mp3"
            existing = next((c for c in (target, target.with_suffix(".flac")) if c.exists()), None)
            if existing:
                audio_href = f"audio/{existing.name}"
            else:
                name, kind = _to_mp3(track["audio"], target) if convert_mp3 else (None, None)
                if name is None:
                    import shutil
                    shutil.copy2(track["audio"], audio_dir / f"{slug}.flac")
                    name = f"{slug}.flac"
                elif kind == "mp3":
                    converted += 1
                audio_href = f"audio/{name}"

        entry = {
            "slug": slug,
            "title": shown,
            "audio": audio_href,
            "rating": track.get("rating"),
            "preset": meta.get("preset", ""),
            "created": meta.get("created", ""),
            "duration": track.get("duration", ""),
            "key_bpm": track.get("key_bpm", ""),
            "style": track.get("style", ""),
            "lyrics": track.get("lyrics", ""),
            "notes": meta.get("notes", ""),
            "parameters": meta.get("parameters", {}),
            "source_folder": track["name"],
        }
        entries.append(entry)
        (data_dir / f"{slug}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")

    (PLAYLIST_DIR / "index.json").write_text(
        json.dumps({"title": title, "generated": datetime.datetime.now().isoformat(timespec="seconds"),
                    "tracks": entries}, indent=2, ensure_ascii=False), encoding="utf-8")

    sections = []
    for entry in entries:
        rows = "".join(
            f"<tr><td>{PARAM_LABELS.get(k, k)}</td><td>{entry['parameters'][k]}</td></tr>"
            for k in PARAM_KEYS if k in entry["parameters"]
        ) or "<tr><td>no parameters recorded</td><td>—</td></tr>"

        bits = []
        if entry["rating"] is not None:
            bits.append(f"<span>{rating_stars(entry['rating'])}</span>")
        for value in (entry["duration"], entry["key_bpm"], entry["created"][:10]):
            if value and value != "-":
                bits.append(f"<span>{html.escape(str(value))}</span>")

        player = (f'<audio controls preload="none" src="{html.escape(entry["audio"])}"></audio>'
                  if entry["audio"] else '<p class="meta">no audio file</p>')
        note = html.escape(entry["notes"]).replace("\n", "<br>")

        sections.append(f"""<section class="track" id="{html.escape(entry['slug'])}">
  <h2>{html.escape(entry['title'])}</h2>
  <p class="meta">{''.join(bits)}</p>
  {player}
  <!-- EDIT ME: note for this track -->
  <p class="note">{note}</p>
  <details>
    <summary>parameters</summary>
    <table>{rows}</table>
    <p class="prompt">{html.escape(entry['style'][:600])}</p>
    <p class="prompt"><a href="data/{html.escape(entry['slug'])}.json">full record</a></p>
  </details>
</section>""")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    page = (PLAYLIST_TEMPLATE
            .replace("__TITLE__", html.escape(title or "Favorites"))
            .replace("__SUBTITLE__", html.escape(subtitle or f"{len(entries)} tracks"))
            .replace("__INTRO__", intro)
            .replace("__TRACKS__", "\n\n".join(sections))
            .replace("__FOOTER__", f"Generated {stamp}. Audio in audio/, one JSON per track in "
                                   f"data/, the whole playlist in index.json. Edit this file "
                                   f"freely; rebuilding preserves the intro block."))
    page_path.write_text(page, encoding="utf-8")

    unnamed = sum(1 for t in favs
                  if not read_track_metadata(Path(t["path"])).get("display_name"))
    tail = (f" {unnamed} track(s) have no display name and fell back to the folder name."
            if unnamed else "")
    fmt = f"{converted} converted to MP3" if converted else "audio copied as-is"
    return (f"🌐 Wrote `{page_path}` — {len(entries)} track(s), {fmt}. "
            f"The whole `favorites_page` folder is servable as-is.{tail}"), str(page_path)


def playlist_names(min_rating_label="all"):
    return [t["name"] for t in filter_by_rating(get_track_data(), min_rating_label)]


def step_track(current, min_rating_label="all", step=1):
    """Move through the listing currently on screen, wrapping at both ends."""
    names = playlist_names(min_rating_label)
    if not names:
        return gr.update()
    if current in names:
        return names[(names.index(current) + step) % len(names)]
    return names[0]


# Audio.stop fires when playback ENDS and also when the source is swapped out.
# Without this guard, choosing a track while another plays would stop the old one,
# fire stop, and immediately skip past the track just chosen.
_LAST_SELECT = {"at": 0.0}


def advance_if_autoplay(current, min_rating_label, autoplay):
    """Audio.stop fires when a track finishes; carry on down the list if asked."""
    if not autoplay:
        return gr.update()
    if time.perf_counter() - _LAST_SELECT["at"] < 3.0:
        return gr.update()     # the stop came from a selection, not from an ending
    return step_track(current, min_rating_label, 1)


def now_playing_markdown(track_name, min_rating_label="all"):
    """Which track is loaded, and where it sits in the current list."""
    if not track_name:
        return "<span class='np-idle'>nothing selected</span>"
    names = playlist_names(min_rating_label)
    position = f"{names.index(track_name) + 1}/{len(names)}" if track_name in names else "—"
    meta = read_track_metadata(Path("outputs") / track_name)
    shown = public_title(meta, track_name)
    stars = rating_stars(rating_of(meta))
    bits = [b for b in (f"<b>{html.escape(shown)}</b>", stars, meta.get("preset", "")) if b]
    return f"<span class='np-on'>▶ {position}</span> &nbsp; " + " &nbsp;·&nbsp; ".join(bits)


def star_report_markdown():
    """Comparison of starred against unstarred renders (analysis.py)."""
    try:
        import importlib
        import analysis
        importlib.reload(analysis)
        return analysis.build_report()
    except Exception as exc:
        return f"❌ Could not build the report: {exc}"


def save_favs_preset():
    try:
        import importlib
        import analysis
        importlib.reload(analysis)
        message, name = analysis.save_starred_preset()
    except Exception as exc:
        return f"❌ {exc}", gr.update()
    if name is None:
        return message, gr.update()
    return message, gr.update(choices=list(load_sounds().keys()))


def favorites_playlist_markdown():
    """Ordered list of starred tracks for the Library panel."""
    favs = [t for t in get_track_data()
            if t.get("rating") is not None and t["rating"] >= FAVORITE_THRESHOLD]
    favs.sort(key=lambda t: -(t.get("rating") or 0))
    if not favs:
        return f"*Nothing rated {FAVORITE_THRESHOLD}+ yet.*"
    lines = []
    for i, t in enumerate(favs, 1):
        meta = read_track_metadata(Path(t["path"]))
        title = public_title(meta, t["name"])
        bits = [b for b in (rating_stars(t.get("rating")), meta.get("preset", ""),
                            t.get("duration", ""), t.get("key_bpm", "")) if b and b != "-"]
        lines.append(f"{i}. **{title}**  \n<span style='opacity:.65;font-size:.8em'>{' · '.join(bits)}</span>")
    return "\n".join(lines)


# ==================== RATING ====================
# 0-5, and ABSENT IS NOT ZERO. A missing rating means never judged; a 0 means
# listened to and rejected. Collapsing those two was the defect in the binary
# star: every comparison counted unheard renders as evidence against.
RATING_MAX = 5
RATING_CHOICES = ["—", "0", "1★", "2★", "3★", "4★", "5★"]
FILTER_CHOICES = ["all", "unrated", "0+", "1★+", "2★+", "3★+", "4★+", "5★"]
FAVORITE_THRESHOLD = 4  # what "favorite" now means, for the playlist and the page


def rating_of(meta):
    """Integer 0-5, or None when the track has never been judged."""
    if not isinstance(meta, dict):
        return None
    value = meta.get("rating")
    if value is None:
        if meta.get("favorite"):      # pre-rating sidecars carried a boolean star
            return FAVORITE_THRESHOLD
        return None
    try:
        return max(0, min(RATING_MAX, int(value)))
    except (TypeError, ValueError):
        return None


def rating_label(value):
    if value is None:
        return RATING_CHOICES[0]
    return RATING_CHOICES[min(max(int(value), 0), RATING_MAX) + 1]


def label_to_rating(label):
    """First digit in the label, or None for '—', 'all' and 'unrated'."""
    if not label or label in (RATING_CHOICES[0], "all", "unrated"):
        return None
    digits = re.match(r"\s*(\d)", str(label))
    return int(digits.group(1)) if digits else None


def rating_stars(value):
    if value is None:
        return ""
    return "✕" if value == 0 else "★" * value


def set_rating(track_name, label):
    """Write a rating into the track's sidecar. Returns (label, status)."""
    value = label_to_rating(label)
    if not track_name:
        return label, "⚠️ No track selected."

    track_dir = Path("outputs") / track_name
    if not track_dir.is_dir():
        return label, f"❌ Track '{track_name}' not found."

    meta = read_track_metadata(track_dir)
    if not meta:
        meta = {"folder": track_name, "track_title": track_name,
                "created": datetime.datetime.now().isoformat(timespec="seconds"),
                "parameters": {}}

    if label == RATING_CHOICES[0]:
        meta.pop("rating", None)
        meta.pop("rated_at", None)
        meta["favorite"] = False
        note = f"↺ Cleared the rating on `{track_name}`."
    else:
        value = 0 if value is None else value
        meta["rating"] = value
        meta["rated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        meta["favorite"] = value >= FAVORITE_THRESHOLD
        note = f"{rating_stars(value)} `{track_name}` rated {value}/5."

    try:
        (track_dir / "track.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        return label, f"❌ Could not write track.json: {exc}"
    return label, note


def rating_for_track(track_name):
    if not track_name:
        return RATING_CHOICES[0]
    return rating_label(rating_of(read_track_metadata(Path("outputs") / track_name)))


def migrate_ratings():
    """
    favorite:true -> rating 4. favorite:false/absent stays UNRATED, not 0 —
    those tracks were never judged, and recording them as rejections would
    invent data the analysis would then treat as evidence.
    """
    moved = 0
    outputs = Path("outputs")
    if not outputs.is_dir():
        return "No outputs directory."
    for folder in sorted(p for p in outputs.iterdir() if p.is_dir()):
        meta_file = folder / "track.json"
        if not meta_file.exists():
            continue
        meta = read_track_metadata(folder)
        if "rating" in meta:
            continue
        if meta.get("favorite"):
            meta["rating"] = FAVORITE_THRESHOLD
            meta["rated_at"] = meta.get("created",
                                        datetime.datetime.now().isoformat(timespec="seconds"))
            try:
                meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
                moved += 1
            except Exception:
                pass
    return (f"✅ Migrated {moved} starred track(s) to rating {FAVORITE_THRESHOLD}. "
            f"Everything else stays unrated — unheard is not the same as rejected.")


# ==================== PRESET SCORING ====================
# A preset is scored by the renders made with it, so nothing is rated twice.

def _scores_by(field):
    """{name: (mean rating, number of rated renders)} over one track.json field."""
    buckets = {}
    for track in get_track_data():
        meta = read_track_metadata(Path(track["path"])) if track.get("path") else {}
        name = (meta.get(field) or track.get(field) or "").strip()
        value = track.get("rating")
        if not name or value is None:
            continue
        buckets.setdefault(name, []).append(value)
    return {name: (sum(v) / len(v), len(v)) for name, v in buckets.items()}


def sound_scores():
    return _scores_by("sound")


def song_scores():
    return _scores_by("song")


def preset_scores():
    """{preset name: (mean rating, number of rated renders)}"""
    buckets = {}
    for track in get_track_data():
        name = (track.get("preset") or "").strip()
        value = track.get("rating")
        if not name or value is None:
            continue
        buckets.setdefault(name, []).append(value)
    return {name: (sum(v) / len(v), len(v)) for name, v in buckets.items()}


def visible_preset_choices(show_all=False, min_score=3.0, limit=25):
    """
    Factory presets and presets with no rated renders always show — untested is
    not rejected. Everything else needs a mean at or above min_score, capped at
    `limit` entries, best first.
    """
    presets = load_all_presets()
    if show_all:
        return list(presets.keys())

    scores = preset_scores()
    always, scored = [], []
    for name in presets:
        if name in FACTORY_PRESETS:
            always.append(name)
            continue
        score = scores.get(name)
        if score is None:
            always.append(name)
        elif score[0] >= min_score:
            scored.append((score[0], score[1], name))
    scored.sort(reverse=True)
    room = max(limit - len(always), 0)
    return always + [name for _, _, name in scored[:room]]


def refresh_preset_list(show_all=False, current=None):
    choices = visible_preset_choices(show_all)
    if current and current not in choices:
        choices = [current] + choices
    return gr.update(choices=choices)


def _visible(entries, factory, scores, show_all, min_score=3.0, limit=25):
    """Untested is not rejected: anything unrated shows. Rated must earn its place."""
    if show_all:
        return list(entries.keys())
    always, scored = [], []
    for name in entries:
        if name in factory:
            always.append(name)
            continue
        s = scores.get(name)
        if s is None:
            always.append(name)
        elif s[0] >= min_score:
            scored.append((s[0], s[1], name))
    scored.sort(reverse=True)
    room = max(limit - len(always), 0)
    return always + [name for _, _, name in scored[:room]]


def visible_sounds(show_all=False):
    return _visible(load_sounds(), FACTORY_SOUNDS, sound_scores(), show_all)


def visible_songs(show_all=False):
    return _visible(load_songs(), FACTORY_SONGS, song_scores(), show_all)


def refresh_sound_list(show_all=False, current=None):
    choices = visible_sounds(show_all)
    if current and current not in choices:
        choices = [current] + choices
    return gr.update(choices=choices)


def refresh_song_list(show_all=False, current=None):
    choices = visible_songs(show_all)
    if current and current not in choices:
        choices = [current] + choices
    return gr.update(choices=choices)


def _rank_table(title, scores):
    if not scores:
        return f"*No rated renders carry a {title} yet.*"
    rows = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
    lines = [f"| {title.capitalize()} | Mean | Rated renders |", "|---|---|---|"]
    for name, (mean, count) in rows:
        lines.append(f"| {name} | {mean:.1f} | {count} |")
    return "\n".join(lines)


def sound_song_ranking():
    """Which sounds and which songs the ratings favour, separately."""
    return (_rank_table("sound", sound_scores()) + "\n\n"
            + _rank_table("song", song_scores()))


def preset_score_markdown():
    scores = preset_scores()
    if not scores:
        return "*No rated renders yet — rate some tracks and the presets rank themselves.*"
    rows = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
    lines = ["| Preset | Mean | Rated renders |", "|---|---|---|"]
    for name, (mean, count) in rows:
        lines.append(f"| {name} | {mean:.1f} | {count} |")
    return "\n".join(lines)


def format_params_summary(params):
    """One-line human-readable parameter summary from a track.json parameters block."""
    if not params:
        return ""
    legacy = {
        "vocal_wildness_sem_temp": "Vocal Wildness",
        "harmonic_exploration_score_temp": "Harmonic Exploration",
        "semantic_top_p": "Semantic Top-P",
        "score_top_p": "Score Top-P",
        "repetition_penalty": "Repetition Penalty",
        "semantic_repetition_penalty": "Vocal Repetition",
        "score_repetition_penalty": "Score Repetition",
    }
    bits = [f"{PARAM_LABELS[k]}: {params[k]}" for k in PARAM_KEYS if k in params]
    bits += [f"{label}: {params[key]}" for key, label in legacy.items() if key in params]
    return " · ".join(bits)


def update_folder_preview(custom_title, lyrics_text, preset_name="", params=None, append_tag=True):
    """Preview of the folder this render will write. HHMMSS is filled in at render time."""
    song_id = sanitize_song_id(custom_title) or sanitize_song_id(extract_lyric_keywords(lyrics_text)) or "YuE_Track"
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    tag = build_param_delta_tag(preset_name, params) if append_tag else ""
    suffix = f"_{tag}" if tag else ""
    note = ("Only settings that differ from the preset are tagged. "
            "Full parameters live in the folder's track.json and in the FLAC tags.")
    return (f"📁 **Resulting Folder:** `{today}_HHMMSS_{song_id}{suffix}`  \n"
            f"<span style='opacity:0.65'>{note}</span>")


def refresh_studio_state(custom_title, preset_name, style, lyrics, append_tag, *params):
    """
    One listener per field: refresh the folder preview and the save-state button.
    `params` arrive in PARAM_SPEC order.
    """
    as_dict = dict(zip(PARAM_KEYS, params))
    preview = update_folder_preview(custom_title, lyrics, preset_name, as_dict, append_tag)
    btn = save_button_state(preset_name, style, lyrics, custom_title, *params)
    return preview, btn


def synthesize_audio_step(style, lyrics, abc_text, seed, ode_steps,
                          sem_temp, sem_top_p, rep_pen, cfg_scale, track_title,
                          preset_name="",
                          score_temp=0.75, score_top_p=0.90,
                          score_rep_pen=SCORE_REP_PEN_DEFAULT,
                          sem_top_k=100, sem_pen_win=50,
                          sem_min_tokens=200, sem_max_tokens=9000,
                          score_top_k=30, score_pen_win=100, cot_mode="full",
                          append_tag=True, audio_format=DEFAULT_AUDIO_FORMAT,
                          song_name="",
                          progress=gr.Progress()):
    _CANCEL["stop"] = False
    progress(0.02, desc="Preparing pipeline...")
    pipe = get_pipeline()

    ode_steps = int(ode_steps) if ode_steps is not None else 16
    sem_temp = float(sem_temp) if sem_temp is not None else 1.15
    sem_top_p = float(sem_top_p) if sem_top_p is not None else 0.95
    rep_pen = float(rep_pen) if rep_pen is not None else SEM_REP_PEN_DEFAULT
    cfg_scale = float(cfg_scale) if cfg_scale is not None else 1.0
    seed = int(seed) if seed is not None else 404
    score_temp = float(score_temp) if score_temp is not None else 0.75
    score_top_p = float(score_top_p) if score_top_p is not None else 0.90

    pipe.generation_config = dataclasses.replace(
        pipe.generation_config,
        ode_steps=ode_steps
    )

    request = {
        "style": (style or "").strip(),
        "lyrics": (lyrics or "").strip(),
        "cot": (cot_mode or "full").strip() or "full",
        "seed": seed,
        "abc": abc_text.strip() if abc_text and abc_text.strip() else None,
        "cfg_scale": cfg_scale,
        "abc_sampling": {
            "temperature": float(score_temp),
            "top_p": float(score_top_p),
            "repetition_penalty": float(score_rep_pen),
            "top_k": int(score_top_k),
            "penalty_window": int(score_pen_win),
        },
        "semantic_sampling": {
            "temperature": sem_temp,
            "top_p": sem_top_p,
            "repetition_penalty": rep_pen,
            "top_k": int(sem_top_k),
            "penalty_window": int(sem_pen_win),
            "min_tokens": int(sem_min_tokens),
            "max_tokens": int(sem_max_tokens),
        }
    }

    eff_preset = (preset_name or "").strip()
    params_now = {
        "score_temp": score_temp, "score_top_p": score_top_p,
        "score_rep_pen": score_rep_pen, "score_top_k": score_top_k,
        "score_pen_win": score_pen_win,
        "sem_temp": sem_temp, "sem_top_p": sem_top_p, "rep_pen": rep_pen,
        "sem_top_k": sem_top_k, "sem_pen_win": sem_pen_win,
        "sem_min_tokens": sem_min_tokens, "sem_max_tokens": sem_max_tokens,
        "cfg_scale": cfg_scale, "flow_steps": ode_steps, "seed": seed,
        "cot_mode": cot_mode,
    }
    # Sortable timestamped folder, tagged with whatever differs from the preset.
    tag = build_param_delta_tag(eff_preset, params_now) if append_tag else ""
    folder_name = build_track_folder_name(track_title, lyrics, tag=tag)
    output_dir = Path("outputs") / folder_name
    suffix = 2
    while output_dir.exists():
        output_dir = Path("outputs") / f"{folder_name}-{suffix}"
        suffix += 1
    folder_name = output_dir.name

    output_dir.mkdir(parents=True, exist_ok=True)

    progress(0.15, desc=f"🎶 Generating song ({ode_steps} flow steps)...")
    start = time.perf_counter()

    on_token, cancelled = make_progress_hooks(
        progress, 0.15, 0.80, 4000, "🎶 Generating song", audio_stage=True
    )
    try:
        song = pipe(**request, cancelled=cancelled, on_token=on_token)
        progress(0.98, desc="💾 Writing audio & artifacts...")
        song.save_artifacts(output_dir)
    except InterruptedError:
        try:
            output_dir.rmdir()
        except OSError:
            pass
        return None, "🛑 Generation cancelled.", ""
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return None, f"⚠️ Synthesis failed: {exc}", ""

    # A second name for the same audio. save_artifacts() writes audio.flac and
    # records its hash in result.json, so that file has to stay. A hard link gives
    # the track its readable name without a second 40MB copy on disk.
    named_flac = output_dir / f"{folder_name}.flac"
    default_flac = output_dir / "audio.flac"
    if default_flac.exists() and not named_flac.exists():
        try:
            os.link(default_flac, named_flac)
        except OSError:
            import shutil
            shutil.copy2(default_flac, named_flac)

    elapsed = time.perf_counter() - start
    audio_seconds = len(song.audio) / 48000
    progress(0.99, desc="🗜️ Writing archive format...")
    converted, fmt_note = apply_audio_format(output_dir, folder_name, audio_format)
    audio_path = converted or str(named_flac if named_flac.exists() else default_flac)

    write_track_metadata(
        output_dir, folder_name, track_title, eff_preset, style, lyrics, params_now,
        audio_path=audio_path, elapsed=elapsed, audio_seconds=audio_seconds,
        song_name=song_name
    )

    status_msg = (f"🎉 Rendered in {elapsed:.1f}s | Length: {audio_seconds:.1f}s | "
                  f"`{Path(audio_path).name}` | {fmt_note}")
    return audio_path, status_msg, folder_name


def preview_audio_step(style, lyrics, abc_text, seed, ode_steps,
                       sem_temp, sem_top_p, rep_pen, cfg_scale, track_title,
                       preset_name="",
                       score_temp=0.75, score_top_p=0.90,
                       score_rep_pen=SCORE_REP_PEN_DEFAULT,
                       sem_top_k=100, sem_pen_win=50,
                       sem_min_tokens=200, sem_max_tokens=9000,
                       score_top_k=30, score_pen_win=100, cot_mode="full",
                       append_tag=True, audio_format=DEFAULT_AUDIO_FORMAT,
                       song_name="",
                       progress=gr.Progress()):
    """
    The opening 20 seconds, at the cheapest flow setting.

    Same arguments as synthesize_audio_step so the two share one input list.
    The score is cut first: the AR stage dominates the clock and its cost
    follows the length of the score it is given.
    """
    if not (abc_text or "").strip():
        return None, "Nothing to preview — write a score first, or drop one in.", ""

    short, kept, whole = truncate_abc(abc_text, PREVIEW_SECONDS)
    title = f"{(track_title or 'preview').strip()}__preview"
    audio, status, folder = synthesize_audio_step(
        style, lyrics, short, seed, PREVIEW_FLOW_STEPS,
        sem_temp, sem_top_p, rep_pen, cfg_scale, title, preset_name,
        score_temp, score_top_p, score_rep_pen, sem_top_k, sem_pen_win,
        sem_min_tokens, sem_max_tokens, score_top_k, score_pen_win, cot_mode,
        append_tag, audio_format, song_name, progress=progress
    )
    note = (f"Preview: first **{kept:.0f}s** of **{whole:.0f}s**, "
            f"{PREVIEW_FLOW_STEPS} flow steps. The full score is untouched.")
    return audio, f"{note}\n\n{status}", folder


def one_click_generate_step(style, lyrics, custom_title,
                            score_temp, score_top_p, score_rep_pen, score_top_k, score_pen_win,
                            sem_temp, sem_top_p, rep_pen, sem_top_k, sem_pen_win,
                            sem_min_tokens, sem_max_tokens,
                            cfg_scale, flow_steps, seed, cot_mode,
                            append_tag=True, audio_format=DEFAULT_AUDIO_FORMAT, preset_name="",
                            song_name="",
                            progress=gr.Progress()):
    """Plan ABC score and synthesize audio in a single flow."""
    abc_text, metrics, plan_msg = generate_plan_step(
        style, lyrics, seed, score_temp, score_top_p, score_rep_pen,
        score_top_k=score_top_k, score_pen_win=score_pen_win, cot_mode=cot_mode,
        progress=progress
    )
    if plan_msg.startswith("🛑"):
        return None, abc_text, metrics, plan_msg, ""

    audio_path, synth_msg, folder_name = synthesize_audio_step(
        style, lyrics, abc_text, seed, flow_steps,
        sem_temp, sem_top_p, rep_pen, cfg_scale, custom_title,
        preset_name=preset_name,
        score_temp=score_temp, score_top_p=score_top_p, score_rep_pen=score_rep_pen,
        sem_top_k=sem_top_k, sem_pen_win=sem_pen_win,
        sem_min_tokens=sem_min_tokens, sem_max_tokens=sem_max_tokens,
        score_top_k=score_top_k, score_pen_win=score_pen_win, cot_mode=cot_mode,
        append_tag=append_tag, audio_format=audio_format, song_name=song_name,
        progress=progress
    )
    total_msg = f"{plan_msg} | {synth_msg}"
    return audio_path, abc_text, metrics, total_msg, folder_name


# ==================== TRACK LIBRARY & MANAGEMENT ====================

def get_track_data():
    """Retrieve full details of all rendered tracks."""
    outputs_dir = Path("outputs")
    if not outputs_dir.exists():
        return []

    tracks = []
    # favorites_page holds the generated website, not a render
    skip = {"favorites_page", "FAVS"}
    for d in sorted(outputs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir() and d.name not in skip:
            # Playable audio: the named MP3, else a named FLAC, else audio.flac
            flac = next(iter(sorted(d.glob("*.mp3"))), None)
            if flac is None:
                for f in d.glob("*.flac"):
                    if f.name != "audio.flac":
                        flac = f
                        break
            if not flac and (d / "audio.flac").exists():
                flac = d / "audio.flac"

            score_file = d / "score.abc"
            req_file = d / "request.json"
            res_file = d / "result.json"

            # Parse audio duration
            duration = "-"
            if flac and flac.exists():
                try:
                    import soundfile as sf
                    secs = sf.info(str(flac)).duration
                    duration = f"{int(secs // 60)}m {int(secs % 60):02d}s"
                except Exception:
                    pass
            elif res_file.exists():
                try:
                    res = json.loads(res_file.read_text())
                    secs = res.get("audio_seconds", 0)
                    duration = f"{int(secs // 60)}m {int(secs % 60):02d}s"
                except Exception:
                    pass

            # Parse key/bpm
            key_bpm = "-"
            score_text = ""
            if score_file.exists():
                score_text = score_file.read_text(encoding="utf-8", errors="ignore")
                key_m = re.search(r"K:([A-G][b#]?[m]?)", score_text)
                tempo_m = re.search(r"Q:(?:1/4=)?(\d+)", score_text)
                k = key_m.group(1) if key_m else "?"
                t = tempo_m.group(1) if tempo_m else "?"
                key_bpm = f"{k} · {t} BPM"

            # Parse style & lyrics
            style_prompt = ""
            lyrics_text = ""
            if req_file.exists():
                try:
                    req = json.loads(req_file.read_text())
                    style_prompt = req.get("style", "")
                    lyrics_text = req.get("lyrics", "")
                except Exception:
                    pass

            # Parameters, preset and the star travel with the track in track.json
            meta = read_track_metadata(d)
            params_summary = format_params_summary(meta.get("parameters", {}))
            preset_used = meta.get("preset", "")
            track_rating = rating_of(meta)
            is_favorite = track_rating is not None and track_rating >= FAVORITE_THRESHOLD
            style_prompt = style_prompt or meta.get("style", "")
            lyrics_text = lyrics_text or meta.get("lyrics", "")

            folder_bytes = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            size_str = f"{folder_bytes / (1024 * 1024):.1f} MB" if (flac and flac.exists()) else "Plan Only"
            mtime = datetime.datetime.fromtimestamp(d.stat().st_mtime).strftime("%b %d, %H:%M")

            tracks.append({
                "name": d.name,
                "audio": str(flac) if (flac and flac.exists()) else None,
                "score": score_text,
                "duration": duration,
                "key_bpm": key_bpm,
                "size": size_str,
                "date": mtime,
                "style": style_prompt,
                "lyrics": lyrics_text,
                "params": params_summary,
                "preset": preset_used,
                "favorite": is_favorite,
                "rating": track_rating,
                "path": str(d.resolve())
            })
    return tracks


def filter_by_rating(tracks, min_rating_label):
    """`min_rating_label` is one of FILTER_CHOICES."""
    if not min_rating_label or min_rating_label == "all":
        return tracks
    if min_rating_label == "unrated":
        return [t for t in tracks if t.get("rating") is None]
    floor = label_to_rating(min_rating_label)
    if floor is None:
        return tracks
    return [t for t in tracks if t.get("rating") is not None and t["rating"] >= floor]


def get_track_table(min_rating_label="all", playing=None):
    """Format track table for Gradio Dataframe. `playing` gets a ▶ marker."""
    tracks = filter_by_rating(get_track_data(), min_rating_label)
    rows = []
    for t in tracks:
        mark = "▶" if playing and t["name"] == playing else ""
        stars = rating_stars(t.get("rating"))
        rows.append([f"{mark} {stars}".strip(), t["name"], t["duration"],
                     t["key_bpm"], t["size"], t["date"]])
    return rows


def select_track_by_name(name):
    """Load audio and details for selected track."""
    if not name:
        return None, "", "", "", "", "", "No track selected"

    base = Path("outputs") / name
    playable = next(iter(sorted(base.glob("*.mp3"))), None)
    if playable is None:
        for f in base.glob("*.flac"):
            if f.name != "audio.flac":
                playable = f
                break
    if playable is None and (base / "audio.flac").exists():
        playable = base / "audio.flac"
    flac = str(playable) if playable else None

    _LAST_SELECT["at"] = time.perf_counter()

    score_file = base / "score.abc"
    score_text = score_file.read_text(encoding="utf-8", errors="ignore") if score_file.exists() else ""

    style = ""
    lyrics = ""
    req_file = base / "request.json"
    if req_file.exists():
        try:
            req = json.loads(req_file.read_text())
            style = req.get("style", "")
            lyrics = req.get("lyrics", "")
        except Exception:
            pass

    params_line = ""
    meta_file = base / "track.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            summary = format_params_summary(meta.get("parameters", {}))
            preset_used = meta.get("preset", "")
            style = style or meta.get("style", "")
            lyrics = lyrics or meta.get("lyrics", "")
            parts = []
            if preset_used:
                parts.append(f"**Preset:** {preset_used}")
            if summary:
                parts.append(summary)
            params_line = "  \n".join(parts)
        except Exception:
            pass
    if not params_line:
        params_line = "*No track.json — rendered before parameters were stored with the track.*"

    metrics = parse_abc_metrics(score_text)
    status = f"Loaded `{name}` | Directory: `{base.resolve()}`"
    return flac, score_text, metrics, params_line, style, lyrics, status


def rename_track_action(old_name, new_name):
    """Rename a track folder in outputs/."""
    if not old_name or not new_name:
        return gr.update(), gr.update(), "⚠️ Please select a track and enter a new name."

    old_dir = Path("outputs") / old_name
    clean_new_name = re.sub(r'[\\/\0<>|?*"]', "_", new_name.strip())
    new_dir = Path("outputs") / clean_new_name

    if not old_dir.exists():
        return gr.update(), gr.update(), f"❌ Track '{old_name}' not found."
    if new_dir.exists() and clean_new_name != old_name:
        return gr.update(), gr.update(), f"❌ A track named '{clean_new_name}' already exists."

    try:
        old_dir.rename(new_dir)
        old_named_flac = new_dir / f"{old_name}.flac"
        new_named_flac = new_dir / f"{clean_new_name}.flac"
        if old_named_flac.exists() and not new_named_flac.exists():
            old_named_flac.rename(new_named_flac)

        tracks = get_track_data()
        choices = [t["name"] for t in tracks]
        table_data = get_track_table()
        return (
            gr.update(choices=choices, value=clean_new_name),
            gr.update(value=table_data),
            f"✅ Successfully renamed `{old_name}` to `{clean_new_name}`!"
        )
    except Exception as e:
        return gr.update(), gr.update(), f"❌ Error renaming: {str(e)}"


def reveal_in_finder_action(track_name):
    """Reveal track directory in macOS Finder."""
    if not track_name:
        return "⚠️ No track selected."
    path = (Path("outputs") / track_name).resolve()
    if path.exists():
        subprocess.run(["open", str(path)])
        return f"📂 Opened `{track_name}` in macOS Finder."
    return f"❌ Path not found: {path}"


def play_in_system_action(track_name):
    """Launch audio file in default macOS media player (QuickTime / Music)."""
    if not track_name:
        return "⚠️ Please select a track first."
    base = Path("outputs") / track_name
    flac = None
    for f in base.glob("*.flac"):
        if f.name != "audio.flac":
            flac = f
            break
    if not flac and (base / "audio.flac").exists():
        flac = base / "audio.flac"

    if flac and flac.exists():
        subprocess.run(["open", str(flac.resolve())])
        return f"🔊 Opened `{flac.name}` in macOS System Player."
    return f"❌ No audio FLAC found in `{track_name}`."


def delete_track_action(track_name):
    """Safely delete a track directory in outputs/."""
    noop = tuple(gr.update() for _ in range(8))

    if not track_name:
        return noop + ("⚠️ Please select a track to delete.",)

    outputs_dir = Path("outputs").resolve()
    target_dir = (outputs_dir / track_name).resolve()

    if target_dir == outputs_dir or outputs_dir not in target_dir.parents:
        return noop + (f"❌ Illegal path: {track_name}",)

    if not target_dir.exists():
        return noop + (f"❌ Track '{track_name}' not found.",)

    try:
        import shutil
        shutil.rmtree(target_dir)
        tracks = get_track_data()
        choices = [t["name"] for t in tracks]
        table_data = get_track_table()
        first = choices[0] if choices else None
        audio, score, metrics, params_line, style, lyrics, _st = select_track_by_name(first)
        return (
            gr.update(choices=choices, value=first),
            gr.update(value=table_data),
            audio, score, metrics, params_line, style, lyrics,
            f"🗑️ Deleted track folder `{track_name}`."
        )
    except Exception as e:
        return noop + (f"❌ Error deleting track: {str(e)}",)



# ==================== TOOLTIP LAYER ====================
# ==================== ASSETS ====================
# Stylesheet, scripts and hover text live in files, not in string literals here.
# This block replaces ~1080 lines of embedded CSS/JS/prose: two competing
# stylesheets (one smuggled into head=, one passed to css=) and a 37-entry
# tooltip dictionary.
ASSETS_DIR = root_dir / "assets"


def _asset(name):
    """An asset's text, or '' if it was never vendored. Callers say what a miss costs."""
    path = ASSETS_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


CUSTOM_CSS = _asset("studio.css")
if not CUSTOM_CSS:
    print("note: assets/studio.css not found — the studio will render unstyled.")

# abcjs is not vendored in this repository. It used to fail silently: ABCJS_CODE
# became '', window.ABCJS stayed undefined, renderSheetMusic returned early and
# the panel was blank forever with nothing said. studio.js now writes an
# explanation into the panel, and startup says it once here.
_ABCJS = _asset("abcjs-basic-min.js")
if not _ABCJS:
    print("note: assets/abcjs-basic-min.js not found — the score panel shows ABC "
          "text and section timings, but no engraved staves.")

HEAD_SCRIPTS = (
    f"<script>{_ABCJS}</script>\n"
    f"<script>window.YUE_TIPS = {json.dumps(TOOLTIPS, ensure_ascii=False)};</script>\n"
    f"<script>{_asset('studio.js')}</script>\n"
)

FAQ_PATH = root_dir / "FAQ.md"
FAQ_TEXT = (FAQ_PATH.read_text(encoding="utf-8") if FAQ_PATH.exists()
            else "*FAQ.md is missing from the repository root.*")


with gr.Blocks(title="YuE2 Studio") as demo:
    gr.HTML("""
    <div class="studio-header" style="display:flex; justify-content:space-between;
         align-items:center; margin-bottom:10px;">
      <h1 style="font-size:1.4rem; font-weight:700; margin:0; letter-spacing:-0.01em;">
        YuE2 Studio
      </h1>
      <button id="theme-toggle-btn" class="theme-toggle-btn"
              onclick="window.toggleTheme()">Dark</button>
    </div>
    """)

    migration = migrate_presets()
    if migration:
        print(f"presets.json split: {migration[0]} presets -> {migration[1]} sounds, "
              f"{migration[2]} songs. presets.json is left as it was.")

    _sounds = load_sounds()
    _songs = load_songs()
    default_sound_name = next(iter(_sounds), "")
    default_song_name = next(iter(_songs), "")
    init_p = _sounds.get(default_sound_name, {})
    init_song = _songs.get(default_song_name, {})

    with gr.Tabs():

        # ============================== CREATE ==============================
        # The score used to live on its own tab, so "Plan Score Only" wrote its
        # output somewhere you were not looking. It is a panel on this tab now,
        # and planning opens it.
        with gr.TabItem("Create"):
            last_sound_state = gr.State(default_sound_name)
            last_song_state = gr.State(default_song_name)

            # Two lists, chosen independently: a sound is the 16 parameters, a
            # song is the words and the score. Any sound can play any song.
            with gr.Row(elem_classes=["preset-toolbar-single-row"]):
                with gr.Row(scale=1, elem_classes=["preset-group"]):
                    gr.Markdown("**Sound:**", scale=0, min_width=0,
                                elem_classes=["toolbar-label"])
                    sound_dropdown = gr.Dropdown(
                        choices=visible_sounds(), value=default_sound_name,
                        show_label=False, interactive=True, allow_custom_value=True,
                        info=None, scale=1, min_width=0,
                        elem_classes=["compact-dropdown"], elem_id="tip-sound"
                    )
                    save_sound_btn = gr.Button(
                        "Saved", interactive=False, scale=0, min_width=0,
                        elem_classes=["btn-secondary", "toolbar-btn", "save-preset-btn"],
                        elem_id="tip-save-sound"
                    )
                    revert_sound_btn = gr.Button(
                        "Revert", scale=0, min_width=0,
                        elem_classes=["btn-secondary", "toolbar-btn"]
                    )
                    delete_sound_btn = gr.Button(
                        "🗑", scale=0, min_width=0,
                        elem_classes=["btn-secondary", "toolbar-btn", "icon-btn"]
                    )
                with gr.Row(scale=1, elem_classes=["preset-group"]):
                    gr.Markdown("**Song:**", scale=0, min_width=0,
                                elem_classes=["toolbar-label"])
                    song_dropdown = gr.Dropdown(
                        choices=visible_songs(), value=default_song_name,
                        show_label=False, interactive=True, allow_custom_value=True,
                        info=None, scale=1, min_width=0,
                        elem_classes=["compact-dropdown"], elem_id="tip-song"
                    )
                    save_song_btn = gr.Button(
                        "Saved", interactive=False, scale=0, min_width=0,
                        elem_classes=["btn-secondary", "toolbar-btn", "save-preset-btn"],
                        elem_id="tip-save-song"
                    )
                    revert_song_btn = gr.Button(
                        "Revert", scale=0, min_width=0,
                        elem_classes=["btn-secondary", "toolbar-btn"]
                    )
                    delete_song_btn = gr.Button(
                        "🗑", scale=0, min_width=0,
                        elem_classes=["btn-secondary", "toolbar-btn", "icon-btn"]
                    )
                show_all_presets = gr.Checkbox(
                    label="all", value=False, scale=0, min_width=0,
                    elem_classes=["nowrap-check", "tight-check"],
                    elem_id="tip-show-all-presets"
                )
                preset_status = gr.Markdown("", scale=0, min_width=0,
                                            elem_classes=["toolbar-status-inline"])

            # Scores and prompts written elsewhere come in here. Pasting into the
            # boxes works as it always did; this is for the files.
            with gr.Accordion("Open files — drop a request.json, a .abc score or a prompt.md",
                              open=False, elem_classes=["drop-panel"]):
                dropped_files = gr.File(
                    label="Drag .json, .abc, .md or .txt here, or click to choose",
                    file_count="multiple",
                    file_types=[".json", ".abc", ".md", ".txt"],
                    height=110,
                    elem_id="tip-drop"
                )
                drop_overwrite = gr.Checkbox(
                    value=True, label="replace boxes that already have text",
                    elem_id="tip-drop-overwrite", container=False
                )
                drop_status = gr.Markdown("", elem_classes=["toolbar-status-inline"])

            with gr.Row():
                # ---- what the song is made of
                with gr.Column(scale=5):
                    gr.Markdown("**Track title / folder name**", elem_classes=["field-header-label"])
                    custom_title = gr.Textbox(
                        show_label=False,
                        placeholder="e.g. cyber_hopkins_v1 (or leave blank to auto-name)",
                        value=init_p.get("custom_title", "")
                    )
                    init_preview_text = update_folder_preview(
                        init_p.get("custom_title", ""), init_p.get("lyrics", "")
                    )
                    folder_preview = gr.Markdown(init_preview_text,
                                                 elem_classes=["folder-preview-card"])
                    with gr.Row(elem_classes=["field-header-row", "folder-toggle-row"]):
                        append_tag_check = gr.Checkbox(
                            label="tag folder with changed parameters", value=True,
                            scale=0, min_width=280, elem_classes=["nowrap-check"],
                            elem_id="tip-append-tag"
                        )
                        audio_format_dd = gr.Dropdown(
                            choices=list(AUDIO_FORMATS.keys()), value=DEFAULT_AUDIO_FORMAT,
                            label="", show_label=False, interactive=True, scale=0, min_width=210,
                            elem_classes=["compact-dropdown"], elem_id="tip-audio-format"
                        )

                    gr.Markdown("**Style and production prompt**",
                                elem_classes=["field-header-label", "field-header-spaced"])
                    style_input = gr.Textbox(
                        show_label=False, value=init_p.get("style", ""), lines=7, max_lines=16
                    )

                    gr.Markdown("**Lyrics** — with [Section] tags",
                                elem_classes=["field-header-label", "field-header-spaced"])
                    lyrics_input = gr.Textbox(
                        show_label=False, value=init_p.get("lyrics", ""), lines=8, max_lines=12
                    )

                # ---- how it is made
                with gr.Column(scale=5):
                    with gr.Group():
                        gr.Markdown("**Stage 1 · Score (ABC)** — key, chords, melody, bar structure",
                                    elem_classes=["stage-label"])
                        with gr.Row():
                            score_temp_slider = gr.Slider(0.1, 2.0, value=float(init_p.get("score_temp", 0.75)), step=0.05,
                                                          label="Exploration", elem_id="tip-score-temp")
                            score_top_p_slider = gr.Slider(0.1, 1.0, value=float(init_p.get("score_top_p", 0.9)), step=0.05,
                                                           label="Top-P", elem_id="tip-score-top-p")
                            score_rep_pen_slider = gr.Slider(1.0, 1.3, value=float(param_value(init_p, "score_rep_pen")), step=0.005,
                                                             label="Repetition", elem_id="tip-score-rep")

                        gr.Markdown("**Stage 2 · Semantic (codec)** — timbre, vocal delivery, arrangement",
                                    elem_classes=["stage-label"])
                        with gr.Row():
                            sem_temp_slider = gr.Slider(0.1, 2.0, value=float(init_p.get("sem_temp", 1.15)), step=0.05,
                                                        label="Wildness", elem_id="tip-sem-temp")
                            sem_top_p_slider = gr.Slider(0.1, 1.0, value=float(init_p.get("sem_top_p", 0.95)), step=0.05,
                                                         label="Top-P", elem_id="tip-sem-top-p")
                            rep_pen_slider = gr.Slider(1.0, 1.5, value=float(param_value(init_p, "rep_pen")), step=0.01,
                                                       label="Repetition", elem_id="tip-sem-rep")

                        gr.Markdown("**Stage 3 · Decode and seed**", elem_classes=["stage-label"])
                        with gr.Row():
                            flow_steps_slider = gr.Slider(8, 32, value=int(param_value(init_p, "flow_steps")), step=4,
                                                          label="Flow Steps", elem_id="tip-flow")
                            cfg_slider = gr.Slider(1.0, 3.0, value=float(param_value(init_p, "cfg_scale")), step=0.1,
                                                   label="CFG", elem_id="tip-cfg")
                            seed_input = gr.Number(value=int(param_value(init_p, "seed")), label="Seed",
                                                   precision=0, elem_id="tip-seed")

                        with gr.Accordion("Advanced — candidate pools, memory, length", open=False):
                            gr.Markdown("**Top-K** caps how many candidates survive *before* Top-P. "
                                        "Raising it widens choice without flattening the distribution — "
                                        "this is the melodic-variety control that costs the least legibility.",
                                        elem_classes=["stage-label"])
                            with gr.Row():
                                score_top_k_slider = gr.Slider(5, 200, value=int(param_value(init_p, "score_top_k")), step=5,
                                                               label="Score Top-K", elem_id="tip-score-topk")
                                sem_top_k_slider = gr.Slider(5, 400, value=int(param_value(init_p, "sem_top_k")), step=5,
                                                             label="Vocal Top-K", elem_id="tip-sem-topk")
                            gr.Markdown("**Penalty window** — how many recent tokens the repetition penalty looks back over.",
                                        elem_classes=["stage-label"])
                            with gr.Row():
                                score_pen_win_slider = gr.Slider(1, 100, value=int(param_value(init_p, "score_pen_win")), step=1,
                                                                 label="Score Window", elem_id="tip-score-win")
                                sem_pen_win_slider = gr.Slider(1, 100, value=int(param_value(init_p, "sem_pen_win")), step=1,
                                                               label="Vocal Window", elem_id="tip-sem-win")
                            gr.Markdown("**Length and mode** — token budget for the audio stage, and whether a score is written at all.",
                                        elem_classes=["stage-label"])
                            with gr.Row():
                                sem_min_tokens_slider = gr.Slider(0, 2000, value=int(param_value(init_p, "sem_min_tokens")), step=50,
                                                                  label="Min Length", elem_id="tip-minlen")
                                sem_max_tokens_slider = gr.Slider(1000, 14000, value=int(param_value(init_p, "sem_max_tokens")), step=250,
                                                                  label="Max Length", elem_id="tip-maxlen")
                                cot_mode_dropdown = gr.Dropdown(
                                    choices=["full", "melody", "off"],
                                    value=str(param_value(init_p, "cot_mode")),
                                    label="Score Mode", interactive=True, elem_id="tip-cot"
                                )

                    # Three actions, each doing something the others do not.
                    # There used to be four: "1-Click", "Plan Score Only",
                    # "Synthesize Audio" here and "Synthesize Audio from this
                    # Score" on the score tab — the last two called the same
                    # function with the same arguments.
                    # Two ways to reach audio, each saying which score it uses.
                    # "Generate song" plans a new one and overwrites the panel;
                    # the label used to leave that to be discovered.
                    generate_btn = gr.Button("Generate song  ·  writes a new score",
                                             size="lg",
                                             elem_classes=["btn-primary", "btn-hero"],
                                             elem_id="tip-generate")
                    render_score_btn = gr.Button(
                        "Render the score below  ·  keeps it as written",
                        size="lg", interactive=False,
                        elem_classes=["btn-hero", "btn-hero-alt"],
                        elem_id="tip-render-score"
                    )
                    with gr.Row():
                        plan_btn = gr.Button("Write score only  ·  ~20s", size="sm",
                                             elem_classes=["btn-secondary"], elem_id="tip-plan")
                        preview_btn = gr.Button("Preview 20s", size="sm", interactive=False,
                                                elem_classes=["btn-secondary"],
                                                elem_id="tip-preview")
                        stop_btn = gr.Button("Stop", variant="stop", size="sm", scale=0,
                                             min_width=96, elem_classes=["nowrap-btn"],
                                             elem_id="tip-stop")

                    # One place audio appears, whichever button produced it. The
                    # score panel used to hold a second player and a second status
                    # line, so a render started there reported nowhere visible.
                    studio_status = gr.Markdown("", elem_classes=["render-status"])
                    audio_output = gr.Audio(label="Rendered audio", type="filepath")
                    with gr.Row(elem_classes=["field-header-row"]):
                        gr.Markdown("rate:", elem_classes=["toggle-row-label"])
                        studio_rating = gr.Radio(
                            choices=RATING_CHOICES, value=RATING_CHOICES[0],
                            label="", show_label=False, container=False, visible=False,
                            elem_classes=["rating-radio"], elem_id="tip-rating"
                        )
                        last_render_state = gr.State("")

            # ---- the score, where the thing that produces it can be seen
            with gr.Accordion("Score — ABC notation and staves", open=False) as score_panel:
                with gr.Row():
                    with gr.Column(scale=5):
                        abc_editor = gr.Code(
                            label="ABC notation — editable",
                            language="markdown", lines=16, max_lines=26, value=""
                        )
                        with gr.Row():
                            render_sheet_btn = gr.Button("Draw the staves", size="sm",
                                                         elem_classes=["btn-secondary"])
                            sheet_state = gr.Markdown("", elem_classes=["sheet-state"])
                        metrics_display = gr.Markdown(
                            "*Write a score, or paste ABC above, to see section timings.*"
                        )
                    with gr.Column(scale=5):
                        sheet_html = gr.HTML(
                            '<div id="sheet-music-paper">'
                            '<p class="sheet-placeholder">No score yet. Press '
                            '<b>Write score only</b> or paste ABC on the left.</p></div>'
                        )

        # ============================== LIBRARY =============================
        with gr.TabItem("Library") as library_tab:
            with gr.Row():
                with gr.Column(scale=6):
                    with gr.Row():
                        refresh_lib_btn = gr.Button("Refresh", size="sm",
                                                    elem_classes=["btn-secondary"])
                        reveal_btn = gr.Button("Reveal in Finder", size="sm",
                                               elem_classes=["btn-secondary"])
                        play_system_btn = gr.Button("Play in macOS player", size="sm",
                                                    elem_classes=["btn-secondary"])
                        favs_only_check = gr.Dropdown(
                            choices=FILTER_CHOICES, value="all", label="", show_label=False,
                            interactive=True, scale=0, min_width=150,
                            elem_classes=["compact-dropdown"], elem_id="tip-favs-only"
                        )

                    track_selector = gr.Dropdown(
                        label="Track — pick here, or click a row below",
                        choices=[], interactive=True
                    )

                    track_table = gr.Dataframe(
                        headers=["★", "Track Folder", "Duration", "Key & BPM", "Size", "Created Date"],
                        datatype=["str", "str", "str", "str", "str", "str"],
                        column_widths=["4%", "40%", "12%", "16%", "12%", "16%"],
                        interactive=False, wrap=True
                    )

                    with gr.Accordion("Favourites playlist", open=False) as favs_panel:
                        favs_playlist = gr.Markdown("*No starred tracks yet.*",
                                                    elem_classes=["favs-playlist"])

                    with gr.Group():
                        gr.Markdown("**Rename or delete**", elem_classes=["field-header-label"])
                        with gr.Row():
                            new_name_input = gr.Textbox(
                                label="New name", show_label=False,
                                placeholder="new folder name, e.g. cyber_hopkins_master"
                            )
                            rename_btn = gr.Button("Rename", elem_classes=["btn-secondary"])
                            delete_btn = gr.Button("Delete", variant="stop")
                        rename_status = gr.Markdown("")

                with gr.Column(scale=4):
                    library_audio = gr.Audio(label="Selected track", type="filepath", autoplay=True)
                    now_playing = gr.Markdown("<span class='np-idle'>nothing selected</span>",
                                              elem_classes=["now-playing"])
                    with gr.Row(elem_classes=["field-header-row"]):
                        prev_btn = gr.Button("⏮", size="sm", scale=0, min_width=52,
                                             elem_classes=["nowrap-btn", "btn-secondary"],
                                             elem_id="tip-prev")
                        next_btn = gr.Button("⏭", size="sm", scale=0, min_width=52,
                                             elem_classes=["nowrap-btn", "btn-secondary"],
                                             elem_id="tip-next")
                        autoplay_check = gr.Checkbox(label="continuous", value=True, scale=0,
                                                     min_width=130, elem_classes=["nowrap-check"],
                                                     elem_id="tip-autoplay")
                    with gr.Row(elem_classes=["field-header-row", "rating-row"]):
                        gr.Markdown("rate:", elem_classes=["toggle-row-label"])
                        lib_rating = gr.Radio(
                            choices=RATING_CHOICES, value=RATING_CHOICES[0],
                            label="", show_label=False, container=False,
                            elem_classes=["rating-radio"], elem_id="tip-rating-lib"
                        )
                    with gr.Row(elem_classes=["field-header-row"]):
                        build_page_btn = gr.Button("Build playlist page", size="sm", scale=0,
                                                   min_width=180,
                                                   elem_classes=["nowrap-btn", "btn-primary"],
                                                   elem_id="tip-build-page")
                        export_favs_btn = gr.Button("Export JSON", size="sm", scale=0,
                                                    min_width=130,
                                                    elem_classes=["nowrap-btn", "btn-secondary"],
                                                    elem_id="tip-export-favs")
                        redecode_btn = gr.Button("Rebuild lossless", size="sm", scale=0,
                                                 min_width=160,
                                                 elem_classes=["nowrap-btn", "btn-secondary"],
                                                 elem_id="tip-redecode")
                    with gr.Row(elem_classes=["field-header-row"]):
                        upgrade_steps = gr.Dropdown(
                            choices=[16, 24, 32], value=32, label="", show_label=False,
                            interactive=True, scale=0, min_width=90,
                            elem_classes=["compact-dropdown"], elem_id="tip-upgrade-steps"
                        )
                        upgrade_btn = gr.Button("Re-solve at higher steps", size="sm", scale=0,
                                                min_width=220,
                                                elem_classes=["nowrap-btn", "btn-secondary"],
                                                elem_id="tip-upgrade")

                    with gr.Accordion("Score and section timings", open=True):
                        lib_metrics = gr.Markdown("")
                        library_score = gr.Code(label="ABC score", language="markdown", lines=8)

                    with gr.Accordion("Prompt, parameters and lyrics used", open=False):
                        lib_params = gr.Markdown("", elem_classes=["folder-preview-card"])
                        lib_style = gr.Textbox(label="Style prompt", lines=2, interactive=False)
                        lib_lyrics = gr.Textbox(label="Lyrics", lines=6, interactive=False)

                    with gr.Row(elem_classes=["field-header-row"]):
                        lib_display_name = gr.Textbox(
                            label="", show_label=False, container=False, scale=1,
                            placeholder="display name for the playlist page — e.g. iridescent scaling",
                            elem_id="tip-display-name"
                        )
                        save_name_btn = gr.Button("Set name", size="sm", scale=0, min_width=100,
                                                  elem_classes=["nowrap-btn", "btn-secondary"])

                    with gr.Accordion("Notes for this track", open=False):
                        lib_notes = gr.Textbox(
                            label="", lines=4,
                            placeholder="Accompanying text — appears under this track on the playlist page.",
                            elem_id="tip-notes"
                        )
                        save_notes_btn = gr.Button("Save notes", size="sm",
                                                   elem_classes=["btn-secondary"])

            def refresh_ui(min_rating_label="all", current=None):
                """Rebuild the list without yanking the user off the track they chose."""
                tracks = filter_by_rating(get_track_data(), min_rating_label)
                choices = [t["name"] for t in tracks]
                keep = current if current in choices else (choices[0] if choices else None)
                table = get_track_table(min_rating_label, keep)
                return gr.update(choices=choices, value=keep), gr.update(value=table)

            def on_table_select(evt: gr.SelectData, min_rating_label="all"):
                if evt and evt.index and len(evt.index) > 0:
                    row = evt.index[0]
                    tracks = filter_by_rating(get_track_data(), min_rating_label)
                    if 0 <= row < len(tracks):
                        return tracks[row]["name"]
                return gr.update()

            def export_favorites():
                """Write favorites.json in outputs/ — the input for the favorites webpage."""
                favs = [t for t in get_track_data()
                        if t.get("rating") is not None and t["rating"] >= FAVORITE_THRESHOLD]
                payload = []
                for t in favs:
                    meta = read_track_metadata(Path(t["path"]))
                    payload.append({
                        "folder": t["name"],
                        "title": meta.get("track_title", t["name"]),
                        "preset": meta.get("preset", ""),
                        "created": meta.get("created", ""),
                        "duration": t["duration"],
                        "key_bpm": t["key_bpm"],
                        "audio": t["audio"],
                        "style": t["style"],
                        "lyrics": t["lyrics"],
                        "parameters": meta.get("parameters", {}),
                        "rating": t.get("rating"),
                        "notes": meta.get("notes", ""),
                    })
                out = Path("outputs") / "favorites.json"
                out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                return f"Wrote `{out}` — {len(payload)} favourite(s)."

            track_table.select(on_table_select, inputs=[favs_only_check], outputs=[track_selector])
            library_tab.select(refresh_ui, inputs=[favs_only_check, track_selector], outputs=[track_selector, track_table])
            refresh_lib_btn.click(refresh_ui, inputs=[favs_only_check, track_selector], outputs=[track_selector, track_table])
            favs_only_check.change(refresh_ui, inputs=[favs_only_check, track_selector], outputs=[track_selector, track_table])

            lib_rating.input(
                set_rating,
                inputs=[track_selector, lib_rating],
                outputs=[lib_rating, rename_status]
            ).then(
                refresh_ui, inputs=[favs_only_check, track_selector], outputs=[track_selector, track_table]
            ).then(
                favorites_playlist_markdown, outputs=[favs_playlist]
            )

            export_favs_btn.click(export_favorites, outputs=[rename_status])

            redecode_btn.click(
                redecode_lossless,
                inputs=[track_selector],
                outputs=[library_audio, rename_status]
            )

            upgrade_btn.click(
                upgrade_flow_steps,
                inputs=[track_selector, upgrade_steps],
                outputs=[library_audio, rename_status]
            )

            prev_btn.click(
                lambda current, flt: step_track(current, flt, -1),
                inputs=[track_selector, favs_only_check],
                outputs=[track_selector]
            )
            next_btn.click(
                lambda current, flt: step_track(current, flt, 1),
                inputs=[track_selector, favs_only_check],
                outputs=[track_selector]
            )
            library_audio.stop(
                advance_if_autoplay,
                inputs=[track_selector, favs_only_check, autoplay_check],
                outputs=[track_selector]
            )

            build_page_btn.click(
                lambda: build_favorites_page()[0],
                outputs=[rename_status]
            )

            save_notes_btn.click(
                save_track_notes,
                inputs=[track_selector, lib_notes],
                outputs=[rename_status]
            )

            save_name_btn.click(
                save_display_name,
                inputs=[track_selector, lib_display_name],
                outputs=[rename_status]
            ).then(
                favorites_playlist_markdown, outputs=[favs_playlist]
            )
            lib_display_name.submit(
                save_display_name,
                inputs=[track_selector, lib_display_name],
                outputs=[rename_status]
            )

            track_selector.change(
                rating_for_track, inputs=[track_selector], outputs=[lib_rating]
            )
            track_selector.change(
                now_playing_markdown,
                inputs=[track_selector, favs_only_check],
                outputs=[now_playing]
            )
            track_selector.change(
                get_track_table,
                inputs=[favs_only_check, track_selector],
                outputs=[track_table]
            )
            track_selector.change(
                load_track_notes, inputs=[track_selector], outputs=[lib_notes]
            )
            track_selector.change(
                load_display_name, inputs=[track_selector], outputs=[lib_display_name]
            )
            library_tab.select(favorites_playlist_markdown, outputs=[favs_playlist])
            refresh_lib_btn.click(favorites_playlist_markdown, outputs=[favs_playlist])

            track_selector.change(
                select_track_by_name,
                inputs=[track_selector],
                outputs=[library_audio, library_score, lib_metrics, lib_params,
                         lib_style, lib_lyrics, rename_status]
            )

            # Auto-fill rename box when track changes
            track_selector.change(
                lambda name: name or "",
                inputs=[track_selector],
                outputs=[new_name_input]
            )

            rename_btn.click(
                rename_track_action,
                inputs=[track_selector, new_name_input],
                outputs=[track_selector, track_table, rename_status]
            )

            reveal_btn.click(
                reveal_in_finder_action,
                inputs=[track_selector],
                outputs=[rename_status]
            )

            play_system_btn.click(
                play_in_system_action,
                inputs=[track_selector],
                outputs=[rename_status]
            )

            delete_btn.click(
                delete_track_action,
                inputs=[track_selector],
                outputs=[
                    track_selector, track_table,
                    library_audio, library_score, lib_metrics, lib_params,
                    lib_style, lib_lyrics, rename_status
                ]
            )

        # ============================= ANALYSIS =============================
        with gr.TabItem("Analysis") as analysis_tab:
            with gr.Row(elem_classes=["field-header-row"]):
                refresh_report_btn = gr.Button("Rebuild report", size="sm", scale=0,
                                               min_width=150,
                                               elem_classes=["nowrap-btn", "btn-primary"],
                                               elem_id="tip-star-report")
                save_favs_preset_btn = gr.Button("Save FAVS preset", size="sm", scale=0,
                                                 min_width=170,
                                                 elem_classes=["nowrap-btn", "btn-secondary"],
                                                 elem_id="tip-save-favs-preset")
                report_status = gr.Markdown("", elem_classes=["toolbar-status-inline"])
                migrate_btn = gr.Button("Migrate old stars", size="sm", scale=0,
                                        min_width=170,
                                        elem_classes=["nowrap-btn", "btn-secondary"],
                                        elem_id="tip-migrate")
            star_report = gr.Markdown("*Open this tab to build the report.*",
                                      elem_classes=["star-report"])
            with gr.Accordion("Preset ranking — mean rating of each preset's renders", open=False):
                preset_ranking = gr.Markdown("", elem_classes=["star-report"])

        # ================================ FAQ ===============================
        # Content is FAQ.md at the repository root, so it is editable without
        # touching this file.
        with gr.TabItem("FAQ"):
            gr.Markdown(FAQ_TEXT, elem_classes=["faq-body"])

    # Every parameter component, in PARAM_SPEC order. Each wiring list below is
    # built from this, so adding a parameter means adding it to PARAM_SPEC and here.
    PARAM_COMPONENTS = [
        score_temp_slider, score_top_p_slider, score_rep_pen_slider,
        score_top_k_slider, score_pen_win_slider,
        sem_temp_slider, sem_top_p_slider, rep_pen_slider,
        sem_top_k_slider, sem_pen_win_slider,
        sem_min_tokens_slider, sem_max_tokens_slider,
        cfg_slider, flow_steps_slider, seed_input, cot_mode_dropdown,
    ]
    assert len(PARAM_COMPONENTS) == len(PARAM_SPEC), "PARAM_COMPONENTS must match PARAM_SPEC"

    # ---------------------------------------------------------------- presets
    # A sound touches only the 16 parameters; a song only the words and the
    # score. Neither can overwrite the other's half, which is the whole point.
    sound_outputs = PARAM_COMPONENTS + [preset_status, last_sound_state]
    song_outputs = [style_input, lyrics_input, custom_title, abc_editor,
                    preset_status, last_song_state]

    sound_dropdown.change(select_sound, inputs=[sound_dropdown], outputs=sound_outputs)
    revert_sound_btn.click(revert_sound, inputs=[sound_dropdown], outputs=sound_outputs)
    save_sound_btn.click(
        save_sound,
        inputs=[sound_dropdown] + PARAM_COMPONENTS,
        outputs=[sound_dropdown, preset_status, last_sound_state, save_sound_btn]
    )
    delete_sound_btn.click(delete_sound, inputs=[sound_dropdown],
                           outputs=[sound_dropdown, preset_status])

    song_dropdown.change(select_song, inputs=[song_dropdown], outputs=song_outputs)
    revert_song_btn.click(revert_song, inputs=[song_dropdown], outputs=song_outputs)
    save_song_btn.click(
        save_song,
        inputs=[song_dropdown, custom_title, style_input, lyrics_input, abc_editor],
        outputs=[song_dropdown, preset_status, last_song_state, save_song_btn]
    )
    delete_song_btn.click(delete_song, inputs=[song_dropdown],
                          outputs=[song_dropdown, preset_status])

    # ------------------------------------------------------ the three actions
    def reveal_rating(folder_name):
        """Rating is meaningless until a render exists to rate."""
        if not folder_name:
            return gr.update(visible=False)
        return gr.update(visible=True, value=rating_for_track(folder_name))

    render_inputs = [
        style_input, lyrics_input, abc_editor, seed_input, flow_steps_slider,
        sem_temp_slider, sem_top_p_slider, rep_pen_slider, cfg_slider, custom_title,
        sound_dropdown,
        score_temp_slider, score_top_p_slider, score_rep_pen_slider,
        sem_top_k_slider, sem_pen_win_slider,
        sem_min_tokens_slider, sem_max_tokens_slider,
        score_top_k_slider, score_pen_win_slider, cot_mode_dropdown,
        append_tag_check, audio_format_dd, song_dropdown
    ]

    # While a render runs, every button that would start another one is dead.
    # They used to stay lit, so a second press queued a second render behind the
    # first with no sign that it had.
    ACTIONS = [generate_btn, render_score_btn, plan_btn, preview_btn]

    def lock_actions():
        return [gr.update(interactive=False)] * len(ACTIONS)

    def unlock_actions(abc_text):
        """Back on afterwards — except the two that need a score, if there is none."""
        has_score = bool((abc_text or "").strip())
        return [gr.update(interactive=True), gr.update(interactive=has_score),
                gr.update(interactive=True), gr.update(interactive=has_score)]

    STAVES_CURRENT = "<span class='sheet-ok'>staves match the ABC</span>"
    STAVES_STALE = "<span class='sheet-stale'>ABC changed — press Draw the staves</span>"
    STAVES_NONE = ""

    def staves_mark(abc_text):
        return STAVES_CURRENT if (abc_text or "").strip() else STAVES_NONE

    def score_buttons(abc_text):
        """The two buttons that act on the panel's ABC, and the staves marker."""
        has_score = bool((abc_text or "").strip())
        return (gr.update(interactive=has_score), gr.update(interactive=has_score),
                STAVES_STALE if has_score else STAVES_NONE)

    # Typing in the panel, or a file landing in it, puts the staves out of date
    # and enables the two buttons that need a score to act on.
    abc_editor.change(
        score_buttons, inputs=[abc_editor],
        outputs=[render_score_btn, preview_btn, sheet_state]
    )

    # Score, then audio, in one pass. The score lands in the panel below and the
    # staves are drawn, so the run leaves evidence of how it got there.
    generate_btn.click(
        lock_actions, outputs=ACTIONS
    ).then(
        one_click_generate_step,
        inputs=([style_input, lyrics_input, custom_title] + PARAM_COMPONENTS
                + [append_tag_check, audio_format_dd, sound_dropdown, song_dropdown]),
        outputs=[audio_output, abc_editor, metrics_display, studio_status, last_render_state]
    ).then(
        reveal_rating, inputs=[last_render_state], outputs=[studio_rating]
    ).then(
        None, inputs=[abc_editor], js="(abc) => { window.renderSheetMusic(abc); }"
    ).then(
        staves_mark, inputs=[abc_editor], outputs=[sheet_state]
    ).then(
        unlock_actions, inputs=[abc_editor], outputs=ACTIONS
    )

    # Stage 1 only. Opening the panel is the point: this output used to be
    # written to a tab the user was not on.
    plan_btn.click(
        lock_actions, outputs=ACTIONS
    ).then(
        generate_plan_step,
        inputs=[
            style_input, lyrics_input, seed_input,
            score_temp_slider, score_top_p_slider, score_rep_pen_slider,
            score_top_k_slider, score_pen_win_slider, cot_mode_dropdown
        ],
        outputs=[abc_editor, metrics_display, studio_status]
    ).then(
        lambda: gr.update(open=True), outputs=[score_panel]
    ).then(
        None, inputs=[abc_editor], js="(abc) => { setTimeout(() => window.renderSheetMusic(abc), 200); }"
    ).then(
        staves_mark, inputs=[abc_editor], outputs=[sheet_state]
    ).then(
        unlock_actions, inputs=[abc_editor], outputs=ACTIONS
    )

    # Stages 2 and 3 on whatever ABC is in the panel, hand edits included. Its
    # audio and its status go to the one player above, not to a second one
    # hidden inside the panel.
    render_score_btn.click(
        lock_actions, outputs=ACTIONS
    ).then(
        synthesize_audio_step,
        inputs=render_inputs,
        outputs=[audio_output, studio_status, last_render_state]
    ).then(
        reveal_rating, inputs=[last_render_state], outputs=[studio_rating]
    ).then(
        unlock_actions, inputs=[abc_editor], outputs=ACTIONS
    )

    # The opening 20 seconds, cheap, to hear whether the score is worth the wait.
    preview_btn.click(
        lock_actions, outputs=ACTIONS
    ).then(
        preview_audio_step,
        inputs=render_inputs,
        outputs=[audio_output, studio_status, last_render_state]
    ).then(
        reveal_rating, inputs=[last_render_state], outputs=[studio_rating]
    ).then(
        unlock_actions, inputs=[abc_editor], outputs=ACTIONS
    )

    stop_btn.click(request_cancel, outputs=[studio_status])

    # ------------------------------------------------------------ file intake
    dropped_files.upload(
        load_dropped_files,
        inputs=[dropped_files, custom_title, style_input, lyrics_input, abc_editor,
                drop_overwrite],
        outputs=[custom_title, style_input, lyrics_input, abc_editor,
                 score_panel, drop_status] + PARAM_COMPONENTS
    ).then(
        None, inputs=[abc_editor],
        js="(abc) => { setTimeout(() => window.renderSheetMusic(abc), 200); }"
    )

    studio_rating.input(
        set_rating,
        inputs=[last_render_state, studio_rating],
        outputs=[studio_rating, studio_status]
    )

    # ------------------------------------------------------------ score panel
    # Staves redraw on demand, not on every keystroke: engraving a full score is
    # expensive and the ABC text is authoritative either way.
    render_sheet_btn.click(
        staves_mark, inputs=[abc_editor], outputs=[sheet_state]
    ).then(
        None, inputs=[abc_editor], js="(abc) => { window.renderSheetMusic(abc); }"
    )

    abc_editor.change(
        parse_abc_metrics,
        inputs=[abc_editor],
        outputs=[metrics_display]
    )

    # ------------------------------------------------------- live studio state
    studio_state_inputs = ([custom_title, sound_dropdown, style_input, lyrics_input,
                            append_tag_check] + PARAM_COMPONENTS)
    for comp in studio_state_inputs:
        comp.change(
            refresh_studio_state,
            inputs=studio_state_inputs,
            outputs=[folder_preview, save_sound_btn]
        )

    # Each Save button reports on its own half only.
    for comp in PARAM_COMPONENTS + [sound_dropdown]:
        comp.change(sound_button_state, inputs=[sound_dropdown] + PARAM_COMPONENTS,
                    outputs=[save_sound_btn])
    for comp in [song_dropdown, custom_title, style_input, lyrics_input, abc_editor]:
        comp.change(
            song_button_state,
            inputs=[song_dropdown, custom_title, style_input, lyrics_input, abc_editor],
            outputs=[save_song_btn]
        )

    # ---------------------------------------------------------------- analysis
    analysis_tab.select(star_report_markdown, outputs=[star_report])
    analysis_tab.select(sound_song_ranking, outputs=[preset_ranking])
    refresh_report_btn.click(star_report_markdown, outputs=[star_report])
    refresh_report_btn.click(sound_song_ranking, outputs=[preset_ranking])
    migrate_btn.click(migrate_ratings, outputs=[report_status]).then(
        star_report_markdown, outputs=[star_report]
    ).then(sound_song_ranking, outputs=[preset_ranking])
    save_favs_preset_btn.click(
        save_favs_preset,
        outputs=[report_status, sound_dropdown]
    ).then(star_report_markdown, outputs=[star_report])

    # A rating anywhere changes the analysis and the preset scores everywhere.
    lib_rating.input(star_report_markdown, outputs=[star_report])
    lib_rating.input(sound_song_ranking, outputs=[preset_ranking])
    studio_rating.input(star_report_markdown, outputs=[star_report])

    for source in (show_all_presets.change, lib_rating.input):
        source(refresh_sound_list, inputs=[show_all_presets, sound_dropdown],
               outputs=[sound_dropdown])
        source(refresh_song_list, inputs=[show_all_presets, song_dropdown],
               outputs=[song_dropdown])

    demo.load(refresh_ui, outputs=[track_selector, track_table])


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=False,
        head=HEAD_SCRIPTS,
        theme=gr.themes.Base(primary_hue="violet", neutral_hue="zinc"),
        css=CUSTOM_CSS
    )
