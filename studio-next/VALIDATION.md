# Progress, notation, and listening export validation

18 automated checks passed with `python3 -m unittest discover -s tests -v`.
JavaScript syntax checks passed for the studio and independent listening page.

The new checks verify stage mapping and elapsed time without extrapolating
nonlinear progress, estimates from comparable completed jobs, stopping the clock
on completion, skipping planning for supplied scores, exporting current ratings,
removing demoted songs from a new ZIP, excluding private paths and notes, and
using live metadata rather than frozen demo ratings. Existing audio-seeking,
request validation, source isolation, and mocked render-result checks also pass.

Browser verification confirmed:

- All twenty composition fields survive an interface refresh unchanged.
- The active job shows its own title, total elapsed minutes, three completed
  stages, the active synthesis stage, and two remaining stages.
- A saved track's ABC score engraves as SVG in the sheet-music dialog.
- The same notation engraves in the composer and leaves composition data unchanged.
- The standalone page lists twelve current favorites, has both project links,
  and contains no Create control.
- Its MP3 playback works, and inspecting another song preserves the audio source.
- No console errors appeared in either view.
- The upload ZIP references twelve four- or five-star songs and contains all
  twelve audio files. Its manifest contains no local filesystem paths.

The independent web server was refreshed while the existing engine and detached
render worker continued with unchanged process IDs. No second model was loaded,
no additional render was submitted, and the active creation was not cancelled.
Live deployment to glia.ca was not performed.

The active test subsequently completed successfully. The library grew from 43
to 44 playable tracks. The new result is a 259.9-second, 48 kHz stereo MP3
(about 4.4 MB) with 4,463 characters of saved ABC notation. The independent MP3
copy matched the original engine output by SHA-256. Soundfile read the first
second successfully and confirmed non-silent samples. No subjective listening
assessment is implied. The new track is unrated, so it stays out of the public
favorites page until rated four or five stars.

## Compact Create and title editing

19 automated checks passed after adding isolated title edits and title validation.
Browser checks in a temporary workspace verified that All transfers all twenty
composition fields, saves the previous draft, and Undo restores every field.
Saving after Undo reuses the same draft ID. Editing a field clears the transfer
notice. The extra Create heading and New composition sidebar control are absent;
Start fresh remains available under More. Renaming a track updates the right
panel immediately even after a library refresh, survives a page reload, and
leaves the source files unchanged. Renamed titles are included in the listening
export. No browser errors were observed, and no test data was added to the
personal library.

The duplicate All menu choice was subsequently removed in favor of the retained
New draft from track button. DESIGN SANDBOX and the old-studio link were removed.
An isolated browser check reproduced the refresh-before-rating case and verified
that the first click immediately displayed four active stars and a 4/5 label;
the rating persisted after reload. No personal ratings were changed by this check.

## Settings transfer and score accuracy

19 automated checks and the JavaScript syntax check passed. In an isolated
browser workspace, Send settings to draft changed the seed to the inspected
track’s seed while preserving the draft title, prompt, lyrics, and ABC exactly.
Expanding More put both actions in normal document flow; its bottom remained
12 pixels above the explanatory paragraph.

The previous GUI and Studio Next bundle byte-identical abcjs 6.4.4. Both pass
the complete ABC text directly to renderAbc; the old viewer differs only in
scale and staff width. No notation is inferred from the audio. A completed
render’s original 4,463-character ABC drew both voices and all 1,162 pitched
note elements, matching the parser’s full-source note count through its final
notes. It had zero parser warnings, and later score sections remained
scrollable. A valid eight-note draft drew eight notes without a warning.

The exact saved riverbed clay ABC instead produced 31 parser warnings, including
an invalid tempo, malformed voice text, and an unclosed chord quote. These are
now visible above the preview, with expandable diagnostic messages and exact
saved source. Warnings are inserted as text; no source is repaired or rewritten.
The saved-score title distinguishes the inspected track from the current draft
and playing track. No render was started or stopped during these checks.
