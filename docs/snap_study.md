# Snap study for Common Ground (scout report, 2026-10-16)
Technique and feel only. No Marvel art, names or marks.

## Why Snap feels fast
Six turns, both players act at the same time, hidden picks until both lock in, then one shared reveal. ~3 minute match. One purple button is both End Turn and the timer. A "snap" (double the stakes) button turns waiting into a decision. Brode (GDC 2023): bite-size, low complexity, lots of depth. No mulligan because it would slow the game.

## Card anatomy
Portrait card, cost badge top-left (blue circle), power badge top-right (orange), art in the middle, name as a wordmark at the bottom, rules text hidden until tap. Upgrade tiers add cosmetic layers: frame break (art pops over the frame), 3D parallax (layered 2D art + heightmap, moved by the accelerometer), subtle animation, shiny name, animated frame, foil finishes. Parallax is layered 2D, not 3D models (Unity case study: in-editor tool "CAT").

## Animation inventory (no published durations; defaults below are industry standard)
Hover lift 150ms ease-out; play arc 350-450ms cubic-bezier(.2,.8,.2,1); flip 500-600ms; count-up 400ms; stagger reveals 250ms. Reveal order shown by a glow on the leader's name tag. Each play has a sound/voice line. Snap has a signature stinger and a loud "Opponent Snapped!" banner.

## Two-player feel
Never see the other side's current picks, only prior turns. Waiting state = the timer button and your own hand, no spinners. Interrupts (snap, retreat) are banner + sound.

## Cheap web techniques (vanilla)
- Tilt + 3-layer parallax: CSS vars --px --py --rx --ry, perspective 900px, preserve-3d, layers translateZ 0/20/40 and translate by px*N, radial glare with mix-blend-mode screen. Pointer events set the vars; pointerleave zeroes them.
- Gyro: deviceorientation (gamma/30, (beta-45)/30), low-pass filter, first reading = neutral. iOS needs DeviceOrientationEvent.requestPermission() inside a tap, HTTPS.
- Flip: .flip{transform-style:preserve-3d;transition:transform .55s} .revealed{rotateY(180deg)} faces backface-visibility hidden.
- Play arc: Web Animations API, 420ms.
- Count-up: rAF + easeOutCubic 400ms. Particles: canvas, 40 sparks, 700ms. Haptics: navigator.vibrate(15) (Android only; pair with sound).
- Refs: github.com/simeydotme/pokemon-cards-css (GPL-3), github.com/simeydotme/hover-tilt (web component), github.com/codeweb-dev/holo-card (MIT, gyro hook), PlayCanvas gyro card demo.

## Proposed mapping for Common Ground
One round, simultaneous, hidden until both commit, one reveal, one stakes moment.
Each phone: fan of 5-6 taste cards (wordmark bottom, category icon top-left, top-right badge becomes match score at reveal). Drag 2 to your slot (face down to the partner). One big button "Lock in" = timer. Optional "I bet we match" wager doubles Common Ground points. Both locked: staggered flip, matches pulse gold, meter fills with count-up. Jukebox card deals in from off-screen with parallax + frame break as the reward layer.

Priority (solo-dev hours):
1. Reveal flip, staggered, both phones in sync from one server timestamp (4-6h). This IS the game.
2. Match pulse + meter count-up + vibrate (3-4h).
3. Hand fan, lift on touch, drag to slot (5-8h). Fiddly on mobile.
4. Tilt/gyro parallax on the jukebox card and matched cards only (3-5h). Only a few layered PNGs needed.
5. Lock-in button feedback, "Partner locked in" banner (2h).
6. Waiting state: timer ring, partner's face-down cards idle float (1-2h).
7. Particles on perfect match / jukebox deal (2h).
8. Sound set: lift, place, flip, match chime, stinger; AudioContext unlocked on first tap (2-3h).
Total ~22-32h on top of game logic and networking.

Sources: gdcvault.com/play/1029024, unity.com/vn/resources/marvel-snap, gamedeveloper.com (simultaneous turns), marvelsnapzone.com beginner guide, seasonedgaming.com art of the cards, blog.snap.untapped.gg priority, destructoid snap mechanic, pocketgamer preview, attackofthefanboy upgrade tiers.
