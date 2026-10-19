# Brief: the feel layer for Our Song v2 (branch `feel`, file `static/play.html` only)

Start from the current `main` (pull first). The table was rebuilt on Oct 17 (`docs/game_v2.md` is the
scope): it fits one phone screen and never scrolls, with a fan hand along the bottom and Q-Lou on the
line between the two fields. Everything you animate already works; your job is to make it feel like
a real table with a real dealer. Read `docs/snap_study.md` first (timings and technique; section 6 has
code) and `docs/touchtunes_style.md` (colors and type).

## The pieces, by id

- `#s-table` is the table; its mode is a class on the section: `toss`, `tosswait`, `play`, `playwait`,
  `reveal`. `body.tablemode` is set while the table is up.
- `#themSlots` their three slots (face-down `.card.flip` backs as they place, `.flip.shown` at the
  reveal); `#themMinis` the six tiny backs during the deal; `#meSlots` your three slots with
  labels Most me, Also me, A bit me; `#fan` the hand (`.fcard` wrappers in a 2 x 3 grid at the deal, a row in the rounds; `fitTable()` sets `--cw` and `--cols`; `.fcard.tossing` marked for the toss, `.fcard.played` once placed). Every
  `.card` carries `s-stage`, `s-bar`, `s-road` or `s-porch` (the joker is retired); the frame color and the
  `.suit` glyph come from that.
- `#stage` is Q-Lou's spot on the line. `.qcoin` is the coin that sits there when he is away;
  `.qmini` is his card and `#qsay` his bubble. `qlouSay(text, hold)` queues a line; the stage gets
  `.talking` while he is up (CSS transitions do the rise and sink today). `qlouClear()` empties the
  queue. All lines come from `static/quips.json` through `q(path, salt, vars)`; do not add text.
- `dealIn(ids)` flies `.flyer.back` elements from the stage to the hand (all four at the deal, the
  replacements after the toss). `fly(from, to, img)` is the hand-to-slot flight. `startDrag`
  (fan to slot) and `startSlotDrag` (slot to slot) are pointer-event drags with a `.dragging` ghost.
- `#meter` and `#words` under your field at the reveal; `#themBubble` a nudge landing.
- `#tact` holds the one action button of each mode (`#tossBtn`, `#lockBtn`, `#nextBtn`).
- Thinking screen: `.qlou-big`, `#say`, `#bet`. Jukebox: `#art`, `#yesBtn` ("Yes, 2 tokens"),
  `#noBtn`, `#firstStays`. Token pill: `#tTok` on the table, `#tokN` elsewhere.

## Do these, in order, and stop when they are done

1. **The deal.** Q-Lou rises (the stage goes `.talking`), the four backs leave from his hands (the
   `.qmini` center, not the stage center), slide to their places in the row with a 70 ms stagger and a
   small arc, and flip face-up on landing (a 300 ms rotateY using the existing `.flip` classes). The
   six minis on their side deal out the same beat, upward. A soft card slide sound per card. The
   replacements after a toss do the same thing from the same place.
2. **Q-Lou's rise and sink.** Replace the CSS scale with a real entrance: he comes up out of the
   line (clip from the line upward, 320 ms, overshoot 4 percent), the bubble pops 120 ms after him,
   and when the hold ends he sinks back into the line and the coin reappears with a short gold
   flash. His flames drift on their own while he is up (a 6 s loop of a few pixels of translate on
   the image). Respect the queue: `nextSay()` is the only thing that moves him.
3. **The toss.** A tossed card (`.fcard.tossing`) tilts back 8 degrees and dims as it does now, plus a
   small red X stamp that lands on it with a thump. On "Toss", the tossed cards fly to Q-Lou (to the
   `.qmini` center, scaling to 0.3) 60 ms apart; then the deal from item 1 brings the replacements.
4. **The play.** A card lifts on touch (`translateY(-14px) scale(1.06)`, 150 ms) before the drag
   starts; the ghost travels with a 4 degree tilt and lands in the slot with a squash; a slot that is
   the drop target glows gold (`.slot.over` exists). Empty slots breathe until filled (border opacity
   35 to 60 percent, 2.4 s loop). Their face-down backs get a slow diagonal sheen (6 s loop) so the
   table never sits dead still. Lock in (`#lockBtn`): squash-and-stretch press and a ring burst; on
   the partner's phone the three backs flip over a fraction (they stay face-down) as a tell.
5. **The reveal.** Each flip in `#themSlots` lands with a thud and a 150 ms `scale(1.06)` bounce on
   both cards of that rank; a same card (both have `.match`) gets a gold particle burst (canvas
   overlay, about 40 sparks, 700 ms) centered between the two; a close pair (`.kin`) a soft purple
   pulse. The meter fill gets a tick sound as it climbs.
6. **Nudges.** `#nudges` buttons are emoji today. Replace them with four inline SVG glyphs in the
   palette (two glasses, a pair of eyes, a flame, a laugh; no brand marks), keep the names `cheers`,
   `eyes`, `fire`, `laugh` (the server only accepts those). The bubble on the partner's phone
   (`showBubble`) should enter from the far edge, bounce once between the slots, linger 1.2 s, fade.
7. **Tokens.** On "Yes, 2 tokens" the coin in the button drops into the token pill and `#tokN` ticks
   down by two (both phones, once both said yes). A short stinger on a played song.
8. **Sound.** Web Audio API, no files if possible: card slide, X stamp, lock in (two-note), flip thud,
   same-card chime, meter tick, stinger. AudioContext unlocked on the first tap. A mute toggle in the
   table's top bar (`.tbar`), left of the token pill, a small pill with a speaker glyph drawn in SVG.

## Rules

No em-dashes anywhere in copy. Keep the 17 px base font; there is no "Aa" control, do not add one.
Do not touch the API calls, the polling, the quip file, or any text the server sends. The table must
still fit one screen with no scroll at 360 by 640, 393 by 852 and 430 by 739 (the fit check in the
scratchpad driver is: nothing on `#s-table` has a bottom below `innerHeight`). Respect
`prefers-reduced-motion`: keep the flips, drop particles, tilt and bounces. Commit in small steps with
clear messages, push the branch, and open a pull request against `main` titled
"Feel: the deal, Q-Lou's rise, flips, particles, sound".

How to check your work: `python3 -m pytest -q` must stay green (50 tests; you should not need to touch
them). For a two-phone run without the real Qloo, `QLOO_API_KEY=x` is enough for the app to import;
`tests/conftest.py` shows the fake. Open two browser windows at 430 by 739, start a challenge in one
and join with the code in the other; the whole table runs locally.
