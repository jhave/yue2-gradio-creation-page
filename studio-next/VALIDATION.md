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
no new render was submitted, and the active creation was not cancelled. Full
model completion remained pending at the time of these checks. Live deployment
to glia.ca was not performed.
