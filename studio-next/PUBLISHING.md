# Independent listening page for glia.ca

The next publishing step is a standalone page of selected top songs, hosted on
glia.ca. It should contain a track list, one persistent player, song details and
lyrics where appropriate, and direct links to the model and this interface:

- YuE model source: https://github.com/multimodal-art-projection/YuE
- Gradio GUI and Studio Next: https://github.com/jhave/yue2-gradio-creation-page

This public page should have no composition or generation controls and no
connection to the local renderer. Audio and selected metadata can be exported as
a static bundle, so hosting does not require Python, Gradio, or model weights.
The existing `publish.py` export is a useful starting point; adapt its selected
track manifest to a listening-only version of Studio Next's shared player.

Choose the songs and public metadata before exporting. Include MP3s and display
information, with optional public lyrics and prompts. Exclude local filesystem
paths, drafts, private notes, job logs, request archives, and latent tensors.

This commit records the publishing direction. It does not upload songs or deploy
a glia.ca page. The original local studio and the private copied demo library
remain available for evaluation.
