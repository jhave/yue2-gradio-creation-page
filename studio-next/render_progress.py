"""Observed stages and elapsed time; never extrapolate the engine's nonlinear bar."""
import datetime
import json
import statistics
import time

STAGES = [('connect', 'Connect to engine'), ('plan', 'Plan the score'),
          ('perform', 'Generate the performance'), ('synthesize', 'Synthesize audio'),
          ('save', 'Save audio and archive'), ('copy', 'Add to this library')]


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def stage_for(state):
    if state.get('state') == 'complete':
        return 'complete'
    if state.get('state') == 'connecting':
        return 'connect'
    message = state.get('message', '').lower()
    if 'copying' in message:
        return 'copy'
    if 'writing' in message or 'archive format' in message:
        return 'save'
    if 'synthesizing' in message:
        return 'synthesize'
    if 'generating song' in message:
        return 'perform'
    if 'planning' in message:
        return 'plan'
    return None


def estimate(directory, composition):
    samples = []
    for previous in directory.parent.iterdir():
        if previous == directory or not previous.is_dir():
            continue
        status, request = read(previous / 'status.json'), read(previous / 'request.json')
        if status.get('state') != 'complete' or bool(request.get('score')) != bool(composition.get('score')):
            continue
        params, target = request.get('parameters', {}), composition.get('parameters', {})
        if params.get('flow_steps') != target.get('flow_steps') or params.get('cot_mode') != target.get('cot_mode'):
            continue
        # A short instrumental and a long vocal piece are poor timing comparisons.
        a, b = len(request.get('lyrics', '')) + len(request.get('score', '')) + 100, len(composition.get('lyrics', '')) + len(composition.get('score', '')) + 100
        if not 0.5 <= a / b <= 2:
            continue
        try:
            end = datetime.datetime.fromisoformat(status['updated']).timestamp()
            total = end - (previous / 'request.json').stat().st_mtime
            if total > 0:
                samples.append(total)
        except (OSError, KeyError, ValueError):
            continue
    if len(samples) < 3:
        return None, len(samples)
    median = statistics.median(samples)
    return {'low': min(min(samples), median * .8), 'high': max(max(samples), median * 1.2),
            'median': median}, len(samples)


def enrich(directory, state, now=None):
    now = time.time() if now is None else now
    request_path = directory / 'request.json'
    composition = read(request_path)
    started = request_path.stat().st_mtime if request_path.exists() else now
    timing = read(directory / 'progress.json')
    history = timing.get('history', [])
    phase = stage_for(state) or timing.get('stage', 'connect')
    if state.get('state') not in ('error', 'idle') and phase != timing.get('stage'):
        history.append({'stage': phase, 'at': now})
        timing = {'stage': phase, 'history': history}
        temp = directory / 'progress.tmp'
        temp.write_text(json.dumps(timing))
        temp.replace(directory / 'progress.json')
    finished = state.get('state') in ('complete', 'error')
    try:
        end = datetime.datetime.fromisoformat(state['updated']).timestamp() if finished else now
    except (KeyError, ValueError):
        end = now
    elapsed = max(0, end - started)
    stages = []
    use_score = bool(composition.get('score', '').strip()) or composition.get('parameters', {}).get('cot_mode') == 'off'
    order = [key for key, _ in STAGES]
    current = order.index(phase) if phase in order else len(order)
    for index, (key, label) in enumerate(STAGES):
        status = 'done' if index < current else 'active' if index == current else 'remaining'
        if key == 'plan' and use_score:
            status = 'skipped'
            label = 'Use supplied score' if composition.get('score', '').strip() else 'Score planning off'
        if state.get('state') == 'error' and index == current:
            status = 'error'
        observed = next((h['at'] for h in history if h['stage'] == key), None)
        stages.append({'key': key, 'label': label, 'status': status,
                       'observed_at': observed})
    prediction, count = estimate(directory, composition)
    return {**state, 'started_at': started, 'elapsed_seconds': elapsed,
            'stage': phase, 'stages': stages, 'history': history,
            'configured_flow_steps': composition.get('parameters', {}).get('flow_steps'),
            'estimate_samples': count, 'estimated_total': prediction if not finished else None}
