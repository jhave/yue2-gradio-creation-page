# Patches against upstream YuE

The studio runs against a clone of
[multimodal-art-projection/YuE](https://github.com/multimodal-art-projection/YuE)
and needs three small changes to it. They live here because that clone is not
this repository, so nothing else tracks them.

`yue2-src.patch` covers:

| file | what it changes |
|---|---|
| `src/yue2/pipeline.py` | cancellation and per-token progress hooks, so a render can be stopped and reported on |
| `src/yue2/nar.py` | the same hooks in the flow-solve loop |
| `examples/generate.py` | follows the pipeline signature change |

Apply to a fresh clone:

```bash
cd /path/to/YuE
git apply /path/to/this/repo/patches/yue2-src.patch
```

Regenerate after changing the clone:

```bash
cd /path/to/YuE && git diff > /path/to/this/repo/patches/yue2-src.patch
```
