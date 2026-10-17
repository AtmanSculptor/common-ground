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

Two things shipped on Oct 16 evening that work but feel flat. Do these first; they are the
ones Brent wants to see move:
0a. Swap a card (`#swapBtn` under the hand, once per round; `swapMode()` highlights the hand with
    `.hand.swapping`, a tap on a card calls `doSwap()`, which POSTs `/api/game/{code}/swap` and gets
    the new card back). Today the old card slides off to the right and the new one pops up in place.
    Make it a deal: the old card flies off the bottom edge of the screen face-down (flip to
    `static/deck/back.jpg` while it goes), and the new card deals in from Q-Lou's side of the table
    (from above, face-down, flipping up as it lands in the gap), 450 ms total, with a soft slide
    sound. The other five cards must not jump; hold the grid cell while the cards travel.
0b. Nudges (`#nudges` on the waiting screen: four round buttons, `nudge(name)` POSTs
    `/api/game/{code}/emote`; the partner's phone shows it via `showBubble()` as `#themBubble`
    rising over their field, keyed by `emote.at` so each nudge shows once). Today the glyphs are emoji
    and the bubble just fades up. Replace the emoji with four small SVG glyphs in the palette (two
    glasses, a pair of eyes, a flame, a laugh; no brand marks) drawn inline, keep the names `cheers`,
    `eyes`, `fire`, `laugh` (the server only accepts those). The bubble should fly across the table:
    start at the sender's own field on the sender's phone (a small copy lifting off the button), and on
    the partner's phone enter from the far edge of their field, bounce once between the two card slots,
    linger 1.2 s, fade. A tap on a nudge button should feel like pressing a real button (the same
    squash as `#lockBtn` below). Haptics already fire; keep them.

Then these, in order, and stop when they are done:
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
push the branch, and open a pull request against `main` titled "Feel: swap, nudges, tilt, particles, sound".

How to check your work: `python3 -m pytest -q` must stay green (50 tests; you should not need to touch
them). For a two-phone run without the real Qloo, `QLOO_API_KEY=x` is enough for the app to import;
the test suite's `conftest.py` shows the fake. Open two browser windows at 393 by 852, start a
challenge in one and join with the code in the other; the table, swap and nudges all run locally.
