"""Match a composition to the running studio API without importing its models."""
import math
import re


def build_render_call(composition, schema):
    if not isinstance(composition, dict):
        raise ValueError("Expected a composition")
    for key in ("title", "style", "lyrics", "score"):
        if not isinstance(composition.get(key, ""), str):
            raise ValueError(f"Invalid {key}")
    if not composition.get("style", "").strip():
        raise ValueError("Add a style and production prompt first")
    parameters = composition.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("Invalid generation settings")
    endpoint = "/synthesize_audio_step" if composition.get("score", "").strip() else "/one_click_generate_step"
    definitions = schema[endpoint]["parameters"]
    values = {p["parameter_name"]: p.get("parameter_default") for p in definitions}
    for key in values:
        if key in parameters:
            values[key] = parameters[key]
    values.update(style=composition["style"], lyrics=composition.get("lyrics", ""),
                  append_tag=True, audio_format="mp3 320 + latents", preset_name="Studio default", song_name=None)
    if endpoint == "/synthesize_audio_step":
        values.update(abc_text=composition["score"], track_title=composition.get("title", ""),
                      ode_steps=parameters.get("flow_steps", 12))
    else:
        values["custom_title"] = composition.get("title", "")
    for definition in definitions:
        name = definition["parameter_name"]
        value = values[name]
        typ = definition["type"]
        if typ.get("type") in {"number", "integer"}:
            if type(value) not in {int, float} or not math.isfinite(value):
                raise ValueError(f"Invalid {name}")
            bounds = re.search(r"between ([\d.]+) and ([\d.]+)", typ.get("description", ""))
            if bounds and not float(bounds[1]) <= value <= float(bounds[2]):
                raise ValueError(f"{name} is outside the studio's supported range")
            if typ.get("type") == "integer" and int(value) != value:
                raise ValueError(f"{name} must be an integer")
        if typ.get("enum") and value is not None and value not in typ["enum"]:
            raise ValueError(f"Unsupported {name}")
    minimum, maximum = values.get("sem_min_tokens", 0), values.get("sem_max_tokens", 9000)
    if minimum > maximum:
        raise ValueError("Minimum tokens must not exceed maximum tokens")
    return endpoint, values
