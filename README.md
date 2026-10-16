# Our Song

**Two strangers, one jukebox. Play your cards. Q-Lou finds the song you both love.**

Built for the Qloo Agentic Hackathon 2026. Live demo: see the Devpost submission for the link and test instructions.

## What it is

Our Song is a social game for a bar's pay-to-play jukebox, in the shape of a phone app feature. Two people at the bar, each on their own phone, join one room. Each answers four quick "which is more you?" questions, is dealt six taste cards (Night owl, Country roads, Whiskey, Dog person...), and plays the two that are most them. Both hands flip at once. A line lands under each pair. A Common Ground meter climbs when they agree.

Then Q-Lou, the genie in the jukebox, goes to work: it turns both players' cards into Qloo signals, pulls a pool of artists, scores every one for each person, grades on a curve, and picks the artist whose *weaker* score is highest. Common ground, not a compromise. It says why, in one line written from Qloo facts and checked against them, and brings back the artist's top tracks. "Play it for a credit?" Both say yes and it plays.

Partners, not competitors. Nobody wins over anybody. The song does.

The target product is a feature inside a jukebox app (TouchTunes announced "song battles" on its 2026 roadmap, head-to-head; this is the cooperative version). In that product the app pairs you with someone in the same bar. Here, a room code does that job.

## The agent

`common_ground/qlou.py` is the agent. Every step is logged and streamed to both phones while it thinks.

**The fast path, which is what a bar sees.** Qloo scores every card against a shared pool of artists once, at build time, into `static/matrix.json` (`common_ground/matrix.py`). The pool is the union of each card's own top artists from Qloo and an outside list of what bars actually play (`static/jukebox_seeds.json`, TouchTunes most-played and Billboard year-end), every name resolved to a Qloo entity; an artist Qloo has never met is dropped and counted, never guessed at. Per game:

1. **read** – the played cards and quiz profile, as below.
2. **draft** – local arithmetic over the matrix: a partner's affinity to an artist is the mean of that artist's Qloo scores on the cards they played (a card Qloo was silent on counts as that card's weakest real score), curved per partner, ranked by the weaker score. No call, no wait. Four names from the top of the list go to the phones as a bet.
3. **verify** – the top three are scored live with `filter.results.entities`, for both partners, in parallel. Q-Lou takes the first with a real live signal. If Qloo is having a slow minute (past 8 s), it goes with the matrix and the live check attaches itself when it lands.
4. **why**, **baseline**, **tracks** – as below, with a deadline: a slow extra never holds the song; it attaches itself to the pick when it arrives and the phones pick it up on their next poll. The control experiment is for the results screen and is never waited on.

The matrix keeps itself current: a keeper thread re-resolves the outside list and extends the matrix every week, and rebuilds it every month in the quiet hours (1 to 5 AM Arizona), the old matrix serving until the new one is whole. New artists can also be added by hand through a loopback-only route (`POST /api/admin/matrix/extend`).

Live, two phones, the song arrives in 5 to 10 seconds. Before the matrix the same games took 31 to 55 seconds and 8 calls.

**The full loop**, used when the matrix is missing or has nothing for a pair:

1. **read** – each partner's played cards and quiz profile become one Qloo signal (`signal.interests.tags` + `signal.demographics.audiences`). Every card maps to a tag or audience verified against the live API (`probes/`).
2. **pool** – candidate artists: `/v2/insights` for partner A, for partner B, and for the two signals combined.
3. **score** – every candidate scored for A, then for B, with `filter.results.entities`. Qloo returns exactly 0.765 when it has no signal for an entity; that is treated as no data, never as agreement.
4. **curve** – min-max within each partner. Raw affinities sit near 1.0 and some signals skew high; the spread inside one person's scores is what carries information.
5. **pick** – rank by the weaker of the two curved scores, ties broken by the smaller gap.
6. **explain** – `feature.explainability=true` on the pick, for the drivers shown on the results screen.
7. **baseline** – the control: a plain LLM is asked the same question cold; its guess is resolved with `/search` and scored by Qloo against both partners. The results screen shows guess vs. data.
8. **why** – one line for the two of them, written by the LLM from Qloo facts only (cards, tags, scores). It must name the pick exactly once and nothing Qloo did not return, or it is replaced by the facts.
9. **tracks** – the artist's top tracks from the catalog.

Everything Q-Lou says is backed by a Qloo entity id or it is not said. A "Not that one" vote excludes the artist and runs the loop again, up to three times.

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
  rooms.py                 game state: rooms, sides, phases, votes
  deck.py                  the 45 cards, dealing, the lines under each pair
  quiz.py                  four adaptive "which is more you?" questions over Qloo audiences
  catalog.py               artist -> top tracks (the seam to a real jukebox catalog)
  stats.py                 one JSON line per game; the /stats summary
  llm.py                   OpenRouter client with a daily spend cap
  agent.py, engine.py      the organizer: what do N audience segments share (second mode, at /organize)
static/
  play.html                the game, phone first, no framework
  deck.html, stats.html    the cards as a gallery; results so far
  cards.json               the deck with its verified Qloo signals
  matrix.json              the built matrix (the app rebuilds it through a loopback-only route)
  jukebox_seeds.json       the outside list: artists bars actually play, resolved to Qloo entities at build time
  deck/                    card art (generated to a locked style brief, see probes/ and deck.md history)
tests/                     39 tests, fake Qloo, no network; run in CI on every push
docs/                      the feel brief, the Snap study, the TouchTunes style notes
probes/                    the scripts and logs that verified every card's signal
```

## Run it

```
pip install -r requirements.txt
cp .env.example .env        # QLOO_API_KEY required; OPENROUTER_API_KEY optional
uvicorn app:app --host 0.0.0.0 --port 8080
```

Open `/` on two phones (or one, against the house). `/deck` shows the cards, `/stats` the results, `/organize` the older organizer.

Tests: `pip install -r requirements-dev.txt && python -m pytest -q`.

Pages run with no cookies and no accounts. A room is a four-letter code and two first names, swept after six hours. Every GET from the page carries a cache-buster because the demo sits behind a CDN. The LLM is optional and capped per day; the game finishes without it.

## Honest limits

- Qloo's hackathon catalog has artists, not songs; track choice is a catalog's job, not Qloo's.
- Audience signals are coarser than tag signals; life cards tint the pick, music cards carry it.
- Explainability payloads vary by entity; when nothing useful comes back the results screen just omits that section.
- The house characters are hand-written profiles, not people.
- Judges should play the two-phone version to see the reveal; the solo version exists so one person can see the whole loop in two minutes.

## License

MIT.
