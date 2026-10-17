# Our Song

**Two strangers, one jukebox. Play your cards. Q-Lou finds the song you both love.**

Built for the Qloo Agentic Hackathon 2026. Live demo: see the Devpost submission for the link and test instructions.

## What it is

Our Song is a social game for a bar's pay-to-play jukebox, in the shape of a phone app feature. Two people at the bar, each on their own phone, join one room. Q-Lou, the genie in the jukebox, deals six taste cards to each (Night owl, Country roads, Whiskey, Dog person...). The screen is a table that fits one phone screen: your partner's side faces you, Q-Lou sits on the line between, your hand fans along the bottom. Three rounds, fixed. **Toss:** tap up to three cards that aren't you; fresh ones deal in, and what you tossed counts as "not me". **Play:** drag three cards onto your side in order, Most me, Also me, A bit me; theirs land face-down across from yours as they place them. **Reveal:** Q-Lou flips the pairs rank by rank and calls each one, the meter fills and says why in words ("Same card: Oldies. Close: Whiskey and Pizza. Different: Gym rat and Metal."), and you can tell him Guilty or Objection. Waiting on a slow partner, you can send a nudge across the table.

Then Q-Lou, the genie in the jukebox, goes to work: it turns both players' cards into Qloo signals, pulls a pool of artists, scores every one for each person, grades on a curve, and picks the artist whose *weaker* score is highest. Common ground, not a compromise. It brings back the artist's top tracks and says only what it knows: "Qloo scored it 99 for Brent, 98 for Josie. The plain model guessed Chris Stapleton. I don't guess." A song costs four tokens. Both say yes and it is two each. One says no and Q-Lou offers a backup, while the first song stays on the table for four to whoever still wants it. No tokens, no song. The keepsake says who paid.

Partners, not competitors. Nobody wins over anybody. The song does.

The target product is a feature inside a jukebox app. TouchTunes' March 2026 roadmap names song battles, leaderboards and social feeds, and its app already lets patrons react to songs, share their music taste and send credits to friends; Our Song is the duet mode of a song battle. In that product the app pairs you with someone in the same bar. Here, a room code does that job.

## Why a bar would want it

- The jukebox already runs on credits and already sells the venue a line on screen. Two tokens each is a credit sale that two people agreed to, and a refusal still leaves a buyer; the venue line on the table ("Happy hour till 7") is the surface TouchTunes already sells to bars and sponsors, with daypart and venue targeting (65,000 venues, about 2 million monthly app users, about 35 minutes of engagement per visit, by TouchTunes' own advertising page).
- Bar games keep people in the seat. Buzztime's trial data: players stay 27 minutes longer and spend about 5 dollars more; a trivia night lifted checks by about 20 dollars at one restaurant. Our Song is a two-minute game, not a league, so it fits between songs.
- Taste data moves a match. Match Group's Yuzu saw a 70 percent lift in likes after adding Qloo interests to profiles. Two strangers who just heard "their" song have something to say to each other.

Sources: TouchTunes' March 17, 2026 release and its advertising page; Buzztime's Beond trial (Bar Business); Hospitality Technology on trivia nights; Qloo's Match Group client story.

## The agent

`common_ground/qlou.py` is the agent. Every step is logged and streamed to both phones while it thinks.

**The fast path, which is what a bar sees.** Qloo scores every card against a shared pool of artists once, at build time, into `static/matrix.json` (`common_ground/matrix.py`). The pool is the union of each card's own top artists from Qloo and an outside list of what bars actually play (`static/jukebox_seeds.json`, TouchTunes most-played and Billboard year-end), every name resolved to a Qloo entity; an artist Qloo has never met is dropped and counted, never guessed at. Per game:

1. **read** – the played cards, as below.
2. **draft** – local arithmetic over the matrix: a partner's affinity to an artist is the weighted mean of that artist's Qloo scores on the cards they played (3, 2, 1 for Most me, Also me, A bit me; a card Qloo was silent on counts as that card's weakest real score) minus a share of the cards they tossed, curved per partner, ranked by the weaker score; within a few points the better-known artist wins. No call, no wait. Four names from the top of the list go to the phones as a bet.
3. **verify** – the top three are scored live with `filter.results.entities`, for both partners, in parallel. Q-Lou takes the first with a real live signal. If Qloo is having a slow minute (past 8 s), it goes with the matrix and the live check attaches itself when it lands.
4. **why**, **baseline**, **tracks**, **compare** – as below, with a deadline: a slow extra never holds the song; it attaches itself to the pick when it arrives and the phones pick it up on their next poll. The control experiment and the compare are for the results screen and are never waited on. **compare** is `/v2/analysis/compare` on each partner's own top three artists from the matrix: the tags the two tastes share ("You share: Soul, Indie rock"), music genres first.

The matrix keeps itself current: a keeper thread re-resolves the outside list and extends the matrix every week, and rebuilds it every month in the quiet hours (1 to 5 AM Arizona), the old matrix serving until the new one is whole. New artists can also be added by hand through a loopback-only route (`POST /api/admin/matrix/extend`).

Live, two phones, the song arrives in 5 to 10 seconds. Before the matrix the same games took 31 to 55 seconds and 8 calls.

**The full loop**, used when the matrix is missing or has nothing for a pair:

1. **read** – each partner's played cards become one Qloo signal (a house partner in solo play adds a few audiences) (`signal.interests.tags` + `signal.demographics.audiences`). Every card maps to a tag or audience verified against the live API (`probes/`).
2. **pool** – candidate artists: `/v2/insights` for partner A, for partner B, and for the two signals combined.
3. **score** – every candidate scored for A, then for B, with `filter.results.entities`. Qloo returns exactly 0.765 when it has no signal for an entity; that is treated as no data, never as agreement.
4. **curve** – min-max within each partner. Raw affinities sit near 1.0 and some signals skew high; the spread inside one person's scores is what carries information.
5. **pick** – rank by the weaker of the two curved scores, ties broken by the smaller gap.
6. **explain** – `feature.explainability=true` on the pick, for the drivers shown on the results screen.
7. **baseline** – the control: a plain LLM is asked the same question cold; its guess is resolved with `/search` and scored by Qloo against both partners. The results screen shows guess vs. data.
8. **why** – one line for the two of them, written by the LLM from Qloo facts only (cards, tags, scores). It must name the pick exactly once and nothing Qloo did not return, or it is replaced by the facts.
9. **tracks** – the artist's top tracks from the catalog.

Everything Q-Lou says is backed by a Qloo entity id or it is not said. A "Try another" vote excludes the artist and runs the loop once more; if the second pick is not a yes from both, the game ends without a song.

**Qloo knows artists, not tracks.** The last step is a catalog lookup: Apple's iTunes Search API in the demo (public, no key, 30-second previews), the jukebox's own catalog in production. The seam is one function in `catalog.py`.

## Why Qloo makes this work

A language model asked "country plus dog person, meets rock plus night owl: name an artist both would like" answers from stereotypes. Qloo scores answer from measured affinities. In testing (`probes/verify4.log`): country + dog person vs. rock + night owl gave Elton John, John Mayer and Christine McVie; Latin heat + Taco Tuesday vs. pop star + big city gave Shakira; gospel + trail head vs. jazz + whiskey gave Gregory Porter, Van Morrison and Diana Krall. Swap one card on one side and all five top picks change. The game shows the LLM's cold guess next to Qloo's pick, scored, every time.

Finding the deck took about 210 live calls. Music-genre tags are excellent. Movie-genre and cuisine tags return noise for artists, so nine "screen" cards were parked rather than faked. Qloo returns one fixed default list for an unknown tag (The Intruders, Case, Snow White's Poison Bite, Mashrou' Leila); that became the dead-tag detector.

## Layout

```
app.py                     FastAPI: pages and JSON routes (organizer + game + stats)
common_ground/
  qloo.py                  thin client for the Qloo Insights API
  qlou.py                  the agent (steps above)
  matrix.py                the card matrix: every card scored by Qloo against a shared pool of artists, once; Q-Lou drafts from it in no time
  rooms.py                 game state: rooms, sides, the three rounds, tokens, votes, the house partner's clock
  deck.py                  the 45 cards, dealing, the meter in words
  quiz.py                  the house partners for solo play (a name, a few Qloo audiences each)
  catalog.py               artist -> top tracks (the seam to a real jukebox catalog)
  stats.py                 one JSON line per game; the /stats summary
  llm.py                   OpenRouter client with a daily spend cap
  agent.py, engine.py      the organizer: what do N audience segments share (second mode, at /organize)
static/
  play.html                the game, phone first, no framework; the table fits one screen
  quips.json               everything Q-Lou says at the table, plain text, hand-edited (no LLM at the table)
  screen.html              the venue's vertical screen, read-only, watches one room
  deck.html, stats.html    the cards as a gallery; results so far
  cards.json               the deck with its verified Qloo signals
  matrix.json              the built matrix (the app rebuilds it through a loopback-only route)
  jukebox_seeds.json       the outside list: artists bars actually play, resolved to Qloo entities at build time
  deck/                    card art (generated to a locked style brief, see probes/ and deck.md history)
tests/                     45 tests, fake Qloo, no network; run in CI on every push
docs/                      the feel brief, the Snap study, the TouchTunes style notes, the pub night run sheet
probes/                    the scripts and logs that verified every card's signal
```

## Run it

```
pip install -r requirements.txt
cp .env.example .env        # QLOO_API_KEY required; OPENROUTER_API_KEY optional
uvicorn app:app --host 0.0.0.0 --port 8080
```

`/screen/<code>` is the venue's big screen: a read-only, vertical (1080 x 1920) view of one room that watches the table, the flip, the ask and the song, for the 43-inch jukebox display TouchTunes ships. Open `/` on two phones, or one with "Try it solo": Q-Lou seats you with a house partner who has a real Qloo profile and picks cards and says yes on a human clock. `/deck` shows the cards, `/stats` the results, `/organize` the older organizer.

Tests: `pip install -r requirements-dev.txt && python -m pytest -q`.

Pages run with no cookies and no accounts. A room is a four-letter code and two nicknames, swept after six hours. Every GET from the page carries a cache-buster because the demo sits behind a CDN. The LLM is optional and capped per day; the game finishes without it.

## Honest limits

Two Qloo features return nothing on the hackathon key as of mid October 2026: `feature.explainability` (the per-entity drivers) and `/v2/trending` (an empty list for any artist we tried). Both are wired behind a loopback probe route and would light up the done screen ("what drove it", "trending this week") on a production key. Qloo also files multi-career celebrities (Justin Timberlake, Miley Cyrus, Selena Gomez) as people rather than artists, so they are not in the pool.

- Qloo's hackathon catalog has artists, not songs; track choice is a catalog's job, not Qloo's.
- Audience signals are coarser than tag signals; life cards tint the pick, music cards carry it.
- The house partners ("Try it solo") are hand-written profiles with first names, not people; they place cards and say yes on a human clock so the table feels like two people.
- Judges can play the whole loop alone with "Try it solo" in about two minutes; the two-phone version is the real thing, and a second phone or an incognito window joins with the room code. `/screen/<code>` shows the same game on the venue's screen.

## License

MIT.
