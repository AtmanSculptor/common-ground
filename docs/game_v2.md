# Our Song, version 2: the scope

Agreed between Brent and BT on Oct 17, 2026, after Brent played v1 on his phone. This is the page
Builder and BT work from. Nothing here is built yet; the current `main` is v1.

## What was wrong with v1

- The table did not fit one screen. On an iPhone 15 Pro Max in Chrome or Safari the bottom row of
  the hand sat under the "Pick two cards" bar, and nothing said you could scroll. The two empty
  fields took 273 px before the first card showed. It felt inflated and cut off at the same time.
- You did not feel your input. Two cards, an optional second round, no weight, and the pick never
  said where it came from.
- Q-Lou claimed things he cannot know ("you both love Colbie Caillat"). That line came from the LLM.
- Q-Lou was wallpaper: a card on the side of the screen, not a presence at the table.

## The rules

Three rounds, fixed, numbered on screen with one line of instruction each. No optional rounds.

1. **Toss.** Q-Lou deals five cards to each partner. Toss up to three; fresh cards come from Q-Lou
   for each one tossed. A tossed card counts as "not me" in the engine (a small negative weight).
   You may keep all five. Lock in.
2. **Play.** Put three cards on your side of the table in order: top slot "Most me", then "Also me",
   then "A bit me". Weights 3, 2, 1. Drag to place, drag to reorder. Lock in. Your partner sees your
   cards face-down as you place them. While one waits, the nudges and Q-Lou's waiting lines.
3. **Reveal.** The pairs flip rank by rank. The meter fills and says in plain words what matched
   ("Cards in common: 1 card the same, 2 close"). Then Q-Lou names the two cards pulling the same
   way and leaves to think. One line, and it stays until "Hear Q-Lou's pick".

Then the bet (four names, call it), then the song.

## Tokens and the song (changed Oct 17 evening after Brent's phone test)

Each phone has its own token balance (10 to start in the demo; the jukebox app sets the real number).
A song costs 4 tokens. Nothing plays until both agree.

- Both thumbs up: 2 tokens from each, and it plays.
- Any thumbs down: the next pick. Three picks in all. The next screen says who passed on what
  ("Pick 2 of 3. Josie passed on La Ley."). The partner who said yes sees it too.
- After the third no: "Sorry, no match tonight." Nobody is charged.
- A yes from someone who cannot pay their half counts as a no.
- Rotation: the last 25 artists Q-Lou picked at this venue sit out of the draft, so a regular does
  not hear the same song twice. (Earlier draft of this page had a solo buy for 4 and a last call;
  dropped. Guilty and Objection dropped too: with one line per round there is nothing to object to.)

## Q-Lou

The dealer and the voice of the table. His spot is the center line between the two fields. He rises
there, deals (cards fly from him to both sides, face-up to you, face-down to the partner), says one
line, and sinks back into the divider leaving a glowing coin. He appears at the start of each round
and at the reveal; the rest of the time the humans have the table.

What he says comes from a quip file, plain text, hand-edited by Brent. No LLM at the table.

- He only talks about what he can see: the cards, the numbers, the clock. Never feelings.
- He is funny about his own limits. "I don't know you. I know what 2,003 artists think of your cards."
- He is sometimes nice to both. "Dog person and Cat person. I've seen this work."
- Lines keyed by: each card (two or three per card, 45 cards); pair kind (same card, close,
  different) and specific pairs where a joke is obvious; toss patterns (tossed three, kept all, both
  tossed the same card: "Noted."); the meter band; the song outcome (both yes, one no, bought alone,
  backup, nothing).
- The song line is numbers only: "Qloo scored it 100 for Brent, 99 for Dee. The plain model guessed
  Marc Anthony. I don't guess."

The LLM stays in one place: the control guess under the hood (a plain model asked the same question
cold, scored by Qloo next to Q-Lou's pick). Model is set by `CG_LLM_MODEL` on the server; default
Gemini 3.5 Flash Lite through OpenRouter, Brent's key. Switching to another model is that one variable.

## The engine (what changes in qlou.py and matrix.py)

- Draft score per artist per partner = weighted mean over the three played cards (3, 2, 1) minus a
  small share of the tossed cards' affinity (not me). Silence still counts as the column floor.
- Everything else stays: curve per partner, the weaker score decides, near ties go to the better
  known artist, verify the top three live, compare for "you share", the control.
- Three weighted cards each instead of four flat across an optional round: at least as much signal,
  and the ranks are information v1 never had.

## The screen

- The table fits one screen, no scrolling: the fan hand along the bottom (cards overlap, slide one
  out), fields sized to the cards, the whole thing sized from the viewport height. Tested at 430 by
  739 (iPhone 15 Pro Max with browser bars), 393 by 852, 360 by 800.
- Round label and one instruction line at the top, always.
- Token pill top right, your own balance.
- Cut from v1: the optional second round, "Deal another round", the swap button (the toss round
  replaces it), the "how Q-Lou got there" line after the game, the LLM why line.
- Keep from v1: the card art and names, the purple and gold palette, the weaker-score rule, the bet,
  the nudges, the meter in words, the keepsake, the big screen, the stats page.

## Who does what

- BT: the rules, the engine, the table layout, the quip file's first draft, tests, deploy.
- Brent: the quip file (cut, rewrite, make it his), play-testing on the phone.
- Builder (branch `feel`, after v2 lands on main): the deal from Q-Lou, his rise and vanish, the
  flips, the reveal particles, the token coin drop, sound, SVG glyphs for the nudges and reactions.
  `docs/feel_brief.md` gets rewritten for v2 when v2 is on main.

## Order of work

1. Engine: weights and tosses in the draft, tests. Half a day.
2. Rooms and API: three rounds, toss, ranked plays, tokens and the vote rules, reactions. Half a day.
3. The table: one screen, fan hand, Q-Lou in the middle with the quip file. A day.
4. Quip file first draft to Brent. Same day as 3.
5. Live test on two phones, then the run sheet, README and Builder's brief.
