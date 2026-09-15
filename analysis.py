#!/usr/bin/env python3
"""
What do the tracks you keep have in common?

Reads every track.json in outputs/ and correlates the 0-5 ratings against
parameters, prompt shape and lyric shape.

Two cautions are built into the report.

UNRATED IS NOT ZERO. A track you never listened to is excluded entirely; a track
you rated 0 is evidence. Collapsing those was the defect in the binary star —
every unheard render counted against itself.

A RATING IS NOT A RANDOM SAMPLE. It is something you chose to make AND then
judged. If you always render at one value, the ratings will show that value
whether or not it helps. So every parameter is reported with its spread across
ALL renders beside its spread among the keepers, and anything that never varied
is named as such instead of given a spurious correlation.

    python3 analysis.py              # print the report
    python3 analysis.py --preset     # also write a FAVS-<date> preset from the weighted medians
"""
import argparse
import datetime
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
PRESETS_FILE = ROOT / "presets.json"

PARAM_ORDER = [
    ("score_temp", "Harmonic Exploration", float),
    ("score_top_p", "Score Top-P", float),
    ("score_rep_pen", "Score Repetition", float),
    ("score_top_k", "Score Top-K", int),
    ("score_pen_win", "Score Window", int),
    ("sem_temp", "Vocal Wildness", float),
    ("sem_top_p", "Semantic Top-P", float),
    ("rep_pen", "Vocal Repetition", float),
    ("sem_top_k", "Vocal Top-K", int),
    ("sem_pen_win", "Vocal Window", int),
    ("sem_min_tokens", "Min Length", int),
    ("sem_max_tokens", "Max Length", int),
    ("cfg_scale", "CFG", float),
    ("flow_steps", "Flow Steps", int),
]

STOPWORDS = {
    "a", "an", "and", "the", "with", "of", "in", "into", "to", "for", "on", "at", "by",
    "from", "as", "is", "are", "be", "then", "that", "this", "it", "its", "or", "but",
    "english", "bpm", "song", "track", "music", "vocals", "vocal", "voice",
}

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]", re.MULTILINE)
METER_RE = re.compile(r"^M:\s*([0-9]+/[0-9]+|C\|?)\s*$", re.MULTILINE)
METER_ANY_RE = re.compile(r"^M:", re.MULTILINE)
CHORD_RE = re.compile(r'"[A-G][^"]{0,14}"')
VOICE_RE = re.compile(r"^V:", re.MULTILINE)
BPM_RE = re.compile(r"(\d{2,3})\s*BPM", re.IGNORECASE)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")


# ---------------------------------------------------------------- loading

def load_tracks():
    tracks = []
    if not OUTPUTS.is_dir():
        return tracks
    for folder in sorted(p for p in OUTPUTS.iterdir() if p.is_dir()):
        if folder.name in {"favorites_page", "FAVS"}:
            continue
        meta_file = folder / "track.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        abc = ""
        score_file = folder / "score.abc"
        if score_file.exists():
            try:
                abc = score_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                abc = ""
        meta["_folder"] = folder.name
        meta["_rating"] = rating_of(meta)
        meta["_features"] = extract_features(meta)
        meta["_features"].update(score_features(abc))
        tracks.append(meta)
    return tracks


FAVORITE_THRESHOLD = 4


def rating_of(meta):
    """0-5, or None for never judged. A boolean star from before the scale reads as 4."""
    value = meta.get("rating")
    if value is None:
        return FAVORITE_THRESHOLD if meta.get("favorite") else None
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return None


def ranks(values):
    """Average ranks, ties shared."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def spearman(xs, ys):
    """
    Rank correlation between a parameter and the rating. Uses the whole 0-5
    gradient instead of splitting into two groups, so 11 rated tracks carry more
    than 23 binary ones did.
    """
    if len(xs) < 4 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def rho_label(rho, n):
    if rho is None:
        return ""
    if n < 8 or abs(rho) < 0.3:
        return ""
    direction = "higher rates better" if rho > 0 else "lower rates better"
    weight = "**" if abs(rho) >= 0.5 else ""
    return f"{weight}{direction}{weight} (ρ {rho:+.2f})"


def weighted_median(pairs):
    """pairs of (value, weight). Weight is the rating, so 5s pull harder than 3s."""
    if not pairs:
        return None
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    if total <= 0:
        return statistics.median([v for v, _ in pairs])
    run = 0
    for value, weight in pairs:
        run += weight
        if run >= total / 2:
            return value
    return pairs[-1][0]


def score_features(abc):
    """
    Metric complexity, read straight from the score the planner wrote.

    A meter is only counted when the directive is well formed. YuE2 sometimes
    emits a chord string into a meter slot ('M:F##dim"c4d4') or an impossible
    signature (5/9); those are counted separately as malformed rather than
    silently inflating the meter count.
    """
    if not abc:
        return {}
    meters = METER_RE.findall(abc)
    standard, irrational = [], []
    for m in meters:
        if m in ("C", "C|"):
            standard.append(m)
            continue
        denominator = m.split("/")[-1]
        if not denominator.isdigit():
            continue
        d = int(denominator)
        # A power-of-2 denominator is conventional notation. Anything else is an
        # irrational meter — five ninth-notes to the bar. Nonstandard, not broken:
        # Ferneyhough, Ades and Carter all write them. abc2midi will refuse them;
        # YuE2's own semantic stage consumed one without complaint.
        (standard if d and (d & (d - 1)) == 0 else irrational).append(m)

    total_directives = len(METER_ANY_RE.findall(abc))
    return {
        "meters_distinct": len(set(standard + irrational)),
        "meters_irrational": len(set(irrational)),
        "meter_changes": total_directives,
        "meters_malformed": max(total_directives - len(standard) - len(irrational), 0),
        "chord_symbols": len(CHORD_RE.findall(abc)),
        "voice_blocks": len(VOICE_RE.findall(abc)),
        "meter_list": sorted(set(standard + irrational)),
        "irrational_list": sorted(set(irrational)),
    }


def extract_features(meta):
    style = meta.get("style", "") or ""
    lyrics = meta.get("lyrics", "") or ""
    style_words = WORD_RE.findall(style.lower())
    lyric_words = WORD_RE.findall(lyrics.lower())
    lyric_lines = [l for l in lyrics.splitlines() if l.strip() and not l.strip().startswith("[")]
    sections = SECTION_RE.findall(lyrics)
    bpm = BPM_RE.search(style)

    return {
        "prompt_words": len(style_words),
        "prompt_commas": style.count(","),
        "prompt_terms": len(set(style_words) - STOPWORDS),
        "bpm": int(bpm.group(1)) if bpm else None,
        "lyric_lines": len(lyric_lines),
        "lyric_words": len(lyric_words),
        "sections": len(sections),
        "section_labels": [s.split("-")[0].strip().lower() for s in sections],
        "avg_line_words": round(len(lyric_words) / len(lyric_lines), 1) if lyric_lines else 0,
        "lexical_variety": round(len(set(lyric_words)) / len(lyric_words), 3) if lyric_words else 0,
        "duration": meta.get("audio_seconds"),
        "render_seconds": meta.get("render_seconds"),
        "style_terms": [w for w in style_words if w not in STOPWORDS and len(w) > 3],
    }


# ---------------------------------------------------------------- statistics

def superiority(starred, rest):
    """
    P(a random starred value > a random unstarred value), ties counted as half.
    Rank-based, no distribution assumed, meaningful at tiny n — 0.5 is no signal.
    """
    if not starred or not rest:
        return None
    wins = sum((1 if a > b else 0.5 if a == b else 0) for a in starred for b in rest)
    return wins / (len(starred) * len(rest))


def spread(values):
    if not values:
        return None
    lo, hi = min(values), max(values)
    return {
        "n": len(values),
        "median": statistics.median(values),
        "min": lo,
        "max": hi,
        "varies": hi - lo > 1e-9,
    }


def fmt(value, caster):
    if value is None:
        return "—"
    return str(int(round(value))) if caster is int else f"{value:g}"


def evidence_label(auc, n_star, n_rest):
    if auc is None:
        return ""
    strength = abs(auc - 0.5)
    if min(n_star, n_rest) < 5 or strength < 0.15:
        return ""
    if strength < 0.25:
        return "leans " + ("higher" if auc > 0.5 else "lower")
    return "**" + ("higher" if auc > 0.5 else "lower") + "**"


# ---------------------------------------------------------------- report

def build_report(tracks=None):
    tracks = tracks if tracks is not None else load_tracks()
    if not tracks:
        return "No track.json files found in outputs/. Render something, or run backfill_metadata.py."

    rated = [t for t in tracks if t["_rating"] is not None]
    unrated = [t for t in tracks if t["_rating"] is None]
    kept = [t for t in rated if t["_rating"] >= FAVORITE_THRESHOLD]

    if not rated:
        return (f"{len(tracks)} renders on file, none rated yet.\n\n"
                "Rate a few in the Library — 0 through 5 — and this report will "
                "correlate each parameter against the ratings.")

    out = []
    out.append(f"## What the {len(rated)} rated tracks say")
    out.append(f"<span style='opacity:.7'>{len(kept)} rated {FAVORITE_THRESHOLD}+ · "
               f"{len(unrated)} never judged and excluded · "
               f"generated {datetime.datetime.now():%Y-%m-%d %H:%M}</span>\n")

    if unrated:
        out.append(f"> **{len(unrated)} render(s) are unrated and count for nothing below.** "
                   f"Unheard is not the same as rejected, so they are left out rather than "
                   f"treated as evidence against. Set the Library filter to *unrated* to work "
                   f"through them — every one you judge, including a 0, sharpens everything here.\n")

    if len(rated) < 8:
        out.append("> **Sample is small.** Correlations are withheld below 8 rated tracks. "
                   "What follows is a description of what you have made, not a finding.\n")

    ratings = [t["_rating"] for t in rated]

    # ---- parameters ----
    out.append("### Parameters\n")
    out.append("| Parameter | All renders | Rated 4+ | Rating correlation |")
    out.append("|---|---|---|---|")

    no_variance, signals = [], []
    for key, label, caster in PARAM_ORDER:
        all_vals = [caster(t["parameters"][key]) for t in tracks
                    if isinstance(t.get("parameters"), dict) and key in t["parameters"]]
        pairs = [(caster(t["parameters"][key]), t["_rating"]) for t in rated
                 if isinstance(t.get("parameters"), dict) and key in t["parameters"]]
        keep_vals = [caster(t["parameters"][key]) for t in kept
                     if isinstance(t.get("parameters"), dict) and key in t["parameters"]]
        if not all_vals:
            continue

        a = spread(all_vals)
        if not a["varies"]:
            no_variance.append((label, fmt(a["median"], caster)))
            continue

        rho = spearman([v for v, _ in pairs], [r for _, r in pairs]) if pairs else None
        note = rho_label(rho, len(pairs))
        if note:
            signals.append((label, note, rho))

        k = spread(keep_vals)
        all_txt = f"{fmt(a['median'], caster)} ({fmt(a['min'], caster)}–{fmt(a['max'], caster)})"
        keep_txt = (f"{fmt(k['median'], caster)} ({fmt(k['min'], caster)}–{fmt(k['max'], caster)})"
                    if k else "—")
        out.append(f"| {label} | {all_txt} | {keep_txt} | {note} |")

    if no_variance:
        out.append("")
        out.append("**Never varied** — one value across every render, so no rating can speak to it:")
        out.append("")
        out.append(", ".join(f"{label} `{value}`" for label, value in no_variance))

    # ---- prompt & lyric shape ----
    out.append("\n### Prompt and lyric shape\n")
    out.append("| Measure | All renders | Rated 4+ | Rating correlation |")
    out.append("|---|---|---|---|")
    shape = [
        ("prompt_words", "Prompt length (words)", int),
        ("prompt_terms", "Distinct prompt terms", int),
        ("bpm", "BPM named in prompt", int),
        ("sections", "Lyric sections", int),
        ("lyric_lines", "Lyric lines", int),
        ("lyric_words", "Lyric words", int),
        ("avg_line_words", "Words per line", float),
        ("lexical_variety", "Lexical variety", float),
        ("duration", "Duration (s)", int),
        ("meters_distinct", "Distinct meters in score", int),
        ("meters_irrational", "Irrational meters", int),
        ("meter_changes", "Meter changes", int),
        ("chord_symbols", "Chord symbols", int),
        ("voice_blocks", "Voice blocks", int),
    ]
    for key, label, caster in shape:
        all_vals = [caster(t["_features"][key]) for t in tracks if t["_features"].get(key)]
        pairs = [(caster(t["_features"][key]), t["_rating"]) for t in rated
                 if t["_features"].get(key)]
        keep_vals = [caster(t["_features"][key]) for t in kept if t["_features"].get(key)]
        if not all_vals:
            continue
        a, k = spread(all_vals), spread(keep_vals)
        rho = spearman([v for v, _ in pairs], [r for _, r in pairs]) if pairs else None
        all_txt = f"{fmt(a['median'], caster)} ({fmt(a['min'], caster)}–{fmt(a['max'], caster)})"
        keep_txt = (f"{fmt(k['median'], caster)} ({fmt(k['min'], caster)}–{fmt(k['max'], caster)})"
                    if k else "—")
        out.append(f"| {label} | {all_txt} | {keep_txt} | {rho_label(rho, len(pairs))} |")

    # ---- metric complexity ----
    metric = [(t, t["_features"].get("meters_distinct")) for t in tracks
              if t["_features"].get("meters_distinct")]
    if metric:
        out.append("\n### Metric complexity\n")
        poly = [(t, n) for t, n in metric if n >= 3]
        malformed = sum(t["_features"].get("meters_malformed", 0) for t, _ in metric)
        if poly:
            out.append(f"{len(poly)} of {len(metric)} scores use 3 or more meters.\n")
            out.append("| Score | Meters | Changes | Rating |")
            out.append("|---|---|---|---|")
            for t, n in sorted(poly, key=lambda x: -x[1]):
                meters = ", ".join(t["_features"].get("meter_list", []))
                rating = "—" if t["_rating"] is None else str(t["_rating"])
                out.append(f"| {t['_folder'][:34]} | {meters} | "
                           f"{t['_features'].get('meter_changes', 0)} | {rating} |")
        else:
            out.append("No score has used three or more meters yet.")
        irrational = sorted({m for t, _ in metric for m in t["_features"].get("irrational_list", [])})
        if irrational:
            out.append(f"\n**Irrational meters emitted:** {', '.join('`' + m + '`' for m in irrational)} "
                       f"— non-power-of-2 denominators. Nonstandard but not broken; abc2midi will "
                       f"refuse them, YuE2's semantic stage accepted them.")
        if malformed:
            out.append(f"\n{malformed} malformed meter directive(s) — a chord string landed in an "
                       f"M: slot. Excluded from the counts above.")

        # does anything in the score stage move it?
        pairs = []
        for key, label, caster in PARAM_ORDER:
            if not key.startswith("score"):
                continue
            xs = [caster(t["parameters"][key]) for t, _ in metric
                  if isinstance(t.get("parameters"), dict) and key in t["parameters"]]
            ys = [n for t, n in metric
                  if isinstance(t.get("parameters"), dict) and key in t["parameters"]]
            if len(set(xs)) < 2:
                pairs.append((label, None))
            else:
                pairs.append((label, spearman(xs, ys)))
        frozen = [label for label, rho in pairs if rho is None]
        if frozen:
            out.append(f"\nThe score-stage parameters that could plausibly drive this — "
                       f"{', '.join(frozen)} — have not varied, so metric complexity here is "
                       f"down to the prompt and the seed. Vary one with the prompt and seed "
                       f"held fixed and this section starts answering the question.")
        else:
            for label, rho in pairs:
                if rho is not None and abs(rho) >= 0.3:
                    out.append(f"\n- **{label}** vs meter count: ρ {rho:+.2f}")

    # ---- vocabulary, weighted by rating ----
    high = [t for t in rated if t["_rating"] >= FAVORITE_THRESHOLD]
    low = [t for t in rated if t["_rating"] < FAVORITE_THRESHOLD]
    high_terms, low_terms = Counter(), Counter()
    for t in high:
        high_terms.update(set(t["_features"]["style_terms"]))
    for t in low:
        low_terms.update(set(t["_features"]["style_terms"]))

    distinctive = []
    for term, count in high_terms.most_common():
        if count < 2:
            continue
        hi = count / len(high) if high else 0
        lo = low_terms.get(term, 0) / len(low) if low else 0
        if hi - lo > 0.15:
            distinctive.append((term, count, hi, lo))

    out.append("\n### Prompt vocabulary\n")
    if not low:
        out.append("Nothing rated below 4 yet, so there is no contrast group. Rate a weak "
                   "render 0-2 and this becomes the most informative section in the report — "
                   "prompt language varies freely even where parameters do not.")
        common = [t for t, c in high_terms.most_common(12) if c >= 2]
        if common:
            out.append("\nMost frequent in high-rated prompts: "
                       + ", ".join(f"`{t}`" for t in common))
    elif distinctive:
        out.append(f"Terms more common in tracks rated {FAVORITE_THRESHOLD}+ than in those rated below:\n")
        out.append("| Term | In 4+ | In 0–3 |")
        out.append("|---|---|---|")
        for term, count, hi, lo in distinctive[:12]:
            out.append(f"| {term} | {count}/{len(high)} ({hi:.0%}) | {lo:.0%} |")
    else:
        out.append("No term separates high from low ratings yet.")

    sections = Counter()
    for t in high:
        sections.update(set(t["_features"]["section_labels"]))
    if sections:
        out.append("\nSection tags in high-rated lyrics: "
                   + ", ".join(f"`{name}` ×{n}" for name, n in sections.most_common(8)))

    # ---- what to do next ----
    out.append("\n### What to vary next\n")
    if unrated:
        out.append(f"- Judge the {len(unrated)} unrated render(s) first. A 0 is as useful as a 5; "
                   f"an unrated track is worth nothing to this report.")
    if no_variance:
        first = no_variance[0][0]
        out.append(f"- {len(no_variance)} parameter(s) have never moved. **{first}** is the place "
                   f"to start: render the same lyrics twice, changing only that, and rate both.")
    for label, note, rho in signals:
        out.append(f"- **{label}** — {note}")
    if not signals and not no_variance and not unrated:
        out.append("Nothing separates the ratings yet. Keep rating.")

    out.append("\n### Rating-weighted settings\n")
    medians = starred_medians(rated)
    if medians:
        labels = dict((k, l) for k, l, _ in PARAM_ORDER)
        out.append(", ".join(f"{labels.get(k, k)} `{v:g}`" for k, v in medians.items()))
        out.append("\n<span style='opacity:.7'>Weighted by rating, so a 5 pulls harder than a 3. "
                   "Save with the button above — a starting point, not a verdict.</span>")
    return "\n".join(out)


def starred_medians(rated=None):
    """Rating-weighted median of each parameter across rated tracks."""
    if rated is None:
        rated = [t for t in load_tracks() if t["_rating"] is not None]
    medians = {}
    for key, label, caster in PARAM_ORDER:
        pairs = [(caster(t["parameters"][key]), t["_rating"]) for t in rated
                 if isinstance(t.get("parameters"), dict) and key in t["parameters"]]
        if pairs:
            value = weighted_median(pairs)
            if value is not None:
                medians[key] = int(round(value)) if caster is int else round(value, 4)
    return medians


def save_starred_preset(name=None):
    """Write a preset from the median of every starred track's settings."""
    tracks = load_tracks()
    rated = [t for t in tracks if t["_rating"] is not None]
    if not rated:
        return "⚠️ No rated tracks to average.", None

    medians = starred_medians(rated)
    if not medians:
        return "⚠️ Rated tracks carry no parameters.", None

    name = name or f"FAVS-{datetime.datetime.now():%Y%m%d-%H%M}"
    entry = dict(medians)
    entry.update({
        "style": "",
        "lyrics": "",
        "custom_title": "",
        "cot_mode": "full",
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "derived_from": f"rating-weighted median of {len(rated)} rated tracks",
    })

    presets = {}
    if PRESETS_FILE.exists():
        try:
            presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            presets = {}
    presets[name] = entry
    PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"💾 Saved preset **{name}** — rating-weighted median of {len(rated)} rated tracks.", name


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", action="store_true", help="also save a FAVS-<date> preset")
    args = ap.parse_args()
    print(build_report())
    if args.preset:
        print()
        print(save_starred_preset()[0])
