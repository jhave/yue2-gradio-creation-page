# Independent listening page for glia.ca

Studio Next creates `glia-page/`, a complete static listening page containing
every available song currently rated four or five stars. Ratings come from the
live original library, with Studio Next's local rating edits applied. The demo
snapshot no longer freezes an original track's current metadata.

The page has a track list, search, sort, lyrics, optional sound descriptions,
and a persistent player. Inspecting another track does not change playback.
It links to [YuE source](https://github.com/multimodal-art-projection/YuE) and
[the Studio / Gradio GUI](https://github.com/jhave/yue2-gradio-creation-page).
There are no creation controls or renderer connections on the public page.

## Local updates

The Studio Next server checks for updates every 30 seconds, including new
tracks, rating promotions or demotions, and public text edits. It rebuilds the
local page when those inputs change. Starting Studio Next again also updates
the page. Completed but unrated songs stay out until given four or five stars.

Preview at http://127.0.0.1:7861/listening/ or open `glia-page/index.html` directly.
When served over HTTP, an open listening page refreshes its catalog every
30 seconds without restarting its player. A page opened directly from disk
needs a browser refresh to read updated files.

For an independent local watcher without the Studio Next web server:

```sh
python3 publish_gallery.py --watch
```

## Prepare an upload

Double-click **Prepare glia upload.command**, or run:

```sh
python3 publish_gallery.py --zip
```

This first rebuilds from current ratings, then creates `glia-page-upload.zip`.
Extract it and upload its contents to any folder on glia.ca. It has relative
asset links and needs no Python, Gradio, or model files on the host. The ZIP
contains only the currently selected songs. A previously made ZIP is a snapshot;
prepare a fresh one after changing the collection.

Local updates do not deploy changes to glia.ca. Refresh the hosted page by
uploading a newly prepared bundle. Deployment has not been configured here.

## Export scope

The page includes playable audio, titles, ratings, dates, duration, lyrics, and
style prompts. Check those public text fields before uploading. It excludes
local filesystem paths, private listening notes, drafts, generation requests,
logs, parameter archives, and latent tensors. Audio is copied, never moved or
modified. Source files and original ratings remain unchanged.

The local output folder retains older generated audio copies to avoid deleting
files automatically. Demoted songs disappear from the manifest and from fresh
upload ZIPs; retained unlisted files are not bundled. Upload the fresh ZIP
contents rather than the entire accumulated local folder.
