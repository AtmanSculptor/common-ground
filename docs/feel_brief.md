# Brief: the feel layer for Our Song (branch `feel`, file `static/play.html` only)

Start from the current `main` (pull first): `static/play.html` was reworked on Oct 16 after a design
review. The screens you touch are now: the hand (`#hand`, cards `.card` with `.sel`), the reveal
(`.card.flip` with `.back` and `.face`; the flip bug is fixed, do not reintroduce `overflow:hidden`
on `.card.flip`), the thinking screen (Q-Lou at card size in `.qlou-big`, one line in `#say`, the
bet in `#bet`), the jukebox (`#art` is the album art and the play button, `#yesBtn` is "Play it"),
and the done screen (`#doneArt`). The small Q-Lou thumbnail in `.qlou` boxes is a secondary spot.

Scope: five animations plus sound, in `static/play.html` and nothing else. No framework, no
build step, no new files except small audio clips under `static/sfx/` (synthesize them with the
Web Audio API if you can; otherwise tiny royalty-free clips, credited in README). Keep the page
under 60 KB of markup and script. Everything must work on a phone in Safari and Chrome.

Read first: `docs/snap_study.md` (technique and timings, section 6 has the code), `docs/touchtunes_style.md`
(colors and type). The game's server API does not change; `state` is polled every 1.2 s and the
screens are driven by `render()`.

Do these, in order, and stop when they are done:
1. Reveal flip stays as is but add a 150 ms stagger-aware `scale(1.06)` bounce on an `exact` match
   and a gold particle burst (canvas overlay, ~40 sparks, 700 ms) on the matched pair only.
2. Hand: cards fan slightly (rotate the six around a shared pivot, 3 to 4 degrees each) and lift on
   touch (`translateY(-24px) scale(1.1)`, 150 ms ease-out). Tapping still selects; no drag needed.
3. Tilt with parallax on the big Q-Lou card (`.qlou-big .card` on the lobby, room code, waiting and
   thinking screens) and on the album art (`#art`, `#doneArt`): pointer-tracked on desktop, gyro on
   phones (DeviceOrientation, with the iOS permission request inside the first tap). Low-pass the
   readings; first reading is neutral. Q-Lou's flames may drift slowly on their own (a 6 s loop of
   a few pixels of translate on the image) so the card never sits dead still.
4. Lock-in button (`#lockBtn`): squash-and-stretch press (`scale(.96)` 80 ms, back with overshoot)
   and a radial ring burst; on the partner's phone a banner slides down from the top bar:
   "<name> locked in". "Play it" (`#yesBtn`) gets the same press plus a coin-drop: the coin in the
   button falls into the credits pill and the count ticks from 5 to 4.
5. Sound: five short cues, generated with the Web Audio API (no files if possible): card lift (soft
   tick), lock in (two-note), reveal flip (whoosh), exact match (chime), "Play it" (short stinger).
   AudioContext unlocked on the first tap. A mute toggle in the top bar next to "Aa".

Rules: no em-dashes anywhere in copy. Keep the 19 px base font and the "Aa" control. Do not touch
the API calls, the polling, or any text the server sends. Respect `prefers-reduced-motion`: with it
on, keep the flips but drop particles, tilt and bounces. Commit in small steps with clear messages,
push the branch, and open a pull request against `main` titled "Feel: fan, tilt, particles, sound".
