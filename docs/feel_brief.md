# Brief: the feel layer for Our Song (branch `feel`, file `static/play.html` only)

Start from the current `main` (pull first): `static/play.html` was reworked twice on Oct 16, the second
time into a table. The screens you touch: the table (`#s-table`, with modes `choosing`, `waiting`,
`revealed` as classes on the section; their field `#themSlots`, your field `#meSlots`, the hand `#hand`,
the quip on the divider `#quip`), the thinking screen (Q-Lou at card size in `.qlou-big`, one line in
`#say`, the bet in `#bet`), the jukebox (`#art` is the album art and the play button, `#yesBtn` is
"Play it"), and the done screen (`#doneArt`). Drag from the hand into your slot already exists (pointer
events, a `.flyer` element that travels from hand to slot); build on it, do not replace it. The flip on
the reveal is `.card.flip` with `.back` (Brent's card back art) and `.face`; the flip bug is fixed, do
not reintroduce `overflow:hidden` on `.card.flip`.

Scope: five animations plus sound, in `static/play.html` and nothing else. No framework, no
build step, no new files except small audio clips under `static/sfx/` (synthesize them with the
Web Audio API if you can; otherwise tiny royalty-free clips, credited in README). Keep the page
under 60 KB of markup and script. Everything must work on a phone in Safari and Chrome.

Read first: `docs/snap_study.md` (technique and timings, section 6 has the code), `docs/touchtunes_style.md`
(colors and type). The game's server API does not change; `state` is polled every 1.2 s and the
screens are driven by `render()`.

Do these, in order, and stop when they are done:
1. Reveal: when their cards flip in `#themSlots`, an `exact` match gets a 150 ms `scale(1.06)` bounce on
   both cards of the pair (theirs and yours, same column) and a gold particle burst (canvas overlay,
   about 40 sparks, 700 ms) centered between the two. A `kin` match gets a soft purple pulse, no sparks.
2. Hand and field: a card lifts on touch (`translateY(-14px) scale(1.06)`, 150 ms ease-out) before the
   drag starts; the `.flyer` travels on a slight arc with a 4 degree tilt and lands with a small squash;
   an empty slot in your field breathes (border opacity 35 to 60 percent, 2.4 s loop) until it is filled.
   Their face-down cards get a slow diagonal sheen (6 s loop) so the table never sits dead still.
3. Tilt with parallax on the big Q-Lou card (`.qlou-big .card` on the lobby, room code, waiting and
   thinking screens) and on the album art (`#art`, `#doneArt`): pointer-tracked on desktop, gyro on
   phones (DeviceOrientation, with the iOS permission request inside the first tap). Low-pass the
   readings; first reading is neutral. Q-Lou's flames drift slowly on their own (a 6 s loop of a few
   pixels of translate on the image).
4. Lock-in (`#lockBtn`): squash-and-stretch press (`scale(.96)` 80 ms, back with overshoot) and a radial
   ring burst; on the partner's phone a banner slides down from the top bar: "<name> locked in" (the
   state already carries `sides[x].locked`). "Play it" (`#yesBtn`) gets the same press plus a coin-drop:
   the coin in the button falls into the credits pill and `#creditN` ticks from 5 to 4.
5. Sound: five short cues, generated with the Web Audio API (no files if possible): card lift (soft
   tick), lock in (two-note), reveal flip (whoosh), exact match (chime), "Play it" (short stinger).
   AudioContext unlocked on the first tap. A mute toggle in the top bar, left of the credits pill, as a
   small pill with a speaker glyph drawn in SVG (no emoji).

Rules: no em-dashes anywhere in copy. Keep the 17 px base font; there is no "Aa" control any more, do
not add one. Do not touch
the API calls, the polling, or any text the server sends. Respect `prefers-reduced-motion`: with it
on, keep the flips but drop particles, tilt and bounces. Commit in small steps with clear messages,
push the branch, and open a pull request against `main` titled "Feel: fan, tilt, particles, sound".
