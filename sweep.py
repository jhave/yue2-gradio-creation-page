#!/usr/bin/env python3
"""
Run a controlled parameter sweep.

The Star Analysis report can only correlate a parameter that has actually moved.
This renders the same prompt, lyrics and seed at a series of values for ONE
parameter, so everything else is held fixed and the difference is attributable.

    # what the report is currently asking for
    python3 sweep.py --preset "fourth world, lo-fi" --param score_temp --values 0.7,0.9,1.1

    # widen the melodic candidate pool instead
    python3 sweep.py --preset "claude-favs_20260914-0731" --param score_top_k --values 30,70,110

    # see the plan without rendering
    python3 sweep.py --preset "..." --param score_temp --values 0.7,1.1 --dry-run

Renders are sequential — one model, one GPU. Each lands in its own folder tagged
with the swept value, and each track.json records the sweep it belongs to so the
report can group them.

Run it while the studio is NOT rendering: both would contend for the same GPU.
Stop it with Ctrl-C between renders; a render already in flight finishes first.
"""
import argparse
import datetime
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", required=True, help="preset name to take prompt, lyrics and settings from")
    ap.add_argument("--param", required=True, help="the one parameter to vary")
    ap.add_argument("--values", required=True, help="comma-separated values for it")
    ap.add_argument("--seed", type=int, help="override the preset's seed (held constant across the sweep)")
    ap.add_argument("--flow-steps", type=int, default=12,
                    help="preview quality for every render (default 12); upgrade keepers later")
    ap.add_argument("--label", default="", help="name for this sweep; defaults to a timestamp")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import app  # the studio's own functions, so the sweep and the GUI agree exactly

    presets = app.load_all_presets()
    if args.preset not in presets:
        sys.exit(f"No preset named {args.preset!r}. Available:\n  "
                 + "\n  ".join(sorted(presets)))
    if args.param not in app.PARAM_KEYS:
        sys.exit(f"Unknown parameter {args.param!r}. One of:\n  " + ", ".join(app.PARAM_KEYS))

    base = presets[args.preset]
    params = dict(zip(app.PARAM_KEYS, app.preset_params(base)))
    caster = dict((k, t) for k, _, t in app.PARAM_SPEC)[args.param]

    try:
        values = [caster(v.strip()) for v in args.values.split(",") if v.strip()]
    except ValueError:
        sys.exit(f"--values must all be {caster.__name__} for {args.param}")
    if len(values) < 2:
        sys.exit("A sweep needs at least two values.")

    seed = args.seed if args.seed is not None else int(params.get("seed", 404))
    params["seed"] = seed
    params["flow_steps"] = int(args.flow_steps)
    label = args.label or f"sweep-{datetime.datetime.now():%Y%m%d-%H%M}"
    title = app.sanitize_song_id(base.get("custom_title") or args.preset) or "sweep"

    print(f"Sweep {label}")
    print(f"  preset      {args.preset}")
    print(f"  varying     {args.param}: {', '.join(str(v) for v in values)}")
    print(f"  held fixed  seed {seed}, flow_steps {params['flow_steps']}, "
          f"everything else from the preset")
    print(f"  folders     <timestamp>_{title}_[{app.PARAM_CODES.get(args.param, args.param)}<value>]")
    print()

    if args.dry_run:
        for v in values:
            print(f"  would render {args.param}={v}")
        print("\nDry run. Drop --dry-run to render.")
        return

    started = time.perf_counter()
    for index, value in enumerate(values, 1):
        run = dict(params)
        run[args.param] = value
        print(f"[{index}/{len(values)}] {args.param}={value} ...", flush=True)
        step_started = time.perf_counter()

        abc, metrics, plan_msg = app.generate_plan_step(
            base.get("style", ""), base.get("lyrics", ""), seed,
            run["score_temp"], run["score_top_p"], run["score_rep_pen"],
            score_top_k=run["score_top_k"], score_pen_win=run["score_pen_win"],
            cot_mode=run["cot_mode"], progress=_silent
        )
        if plan_msg.startswith("🛑") or plan_msg.startswith("❌"):
            print(f"    {plan_msg}")
            continue

        audio_path, synth_msg, folder = app.synthesize_audio_step(
            base.get("style", ""), base.get("lyrics", ""), abc, seed, run["flow_steps"],
            run["sem_temp"], run["sem_top_p"], run["rep_pen"], run["cfg_scale"],
            base.get("custom_title", title),
            preset_name=args.preset,
            score_temp=run["score_temp"], score_top_p=run["score_top_p"],
            score_rep_pen=run["score_rep_pen"],
            sem_top_k=run["sem_top_k"], sem_pen_win=run["sem_pen_win"],
            sem_min_tokens=run["sem_min_tokens"], sem_max_tokens=run["sem_max_tokens"],
            score_top_k=run["score_top_k"], score_pen_win=run["score_pen_win"],
            cot_mode=run["cot_mode"], append_tag=True,
            progress=_silent
        )
        print(f"    {synth_msg}")

        # tag the sidecar so the report can group the sweep
        if folder:
            meta_file = Path("outputs") / folder / "track.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    meta["sweep"] = {"label": label, "param": args.param,
                                     "value": value, "index": index, "of": len(values),
                                     "seed": seed}
                    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
                except Exception:
                    pass
        print(f"    {time.perf_counter() - step_started:.0f}s")

    total = time.perf_counter() - started
    print(f"\nSweep {label} finished in {total / 60:.1f} min.")
    print("Rate every one of them in the Library — including the ones you dislike. "
          "A sweep with only the good renders rated tells the report nothing.")


class _Silent:
    """Stand-in for gr.Progress() when there is no interface attached."""
    def __call__(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return self


_silent = _Silent()


if __name__ == "__main__":
    main()
