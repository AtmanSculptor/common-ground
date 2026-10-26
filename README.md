# Our Song

**Two strangers at a bar, four cards each, and Q-Lou, the genie in the jukebox, finds the one song they would both put on. Then it plays.**

Built for the Qloo Agentic Hackathon 2026. MIT license.

## Try it

**https://boostersplace.com/bt/app/play**

Tap **Who are you?** and give a name, then press **Invite** on anyone in the list. The regulars always say yes. Ninety seconds. On a laptop the page adds a live ledger of every Qloo call beside the phone. For two real phones, one presses **Start a table** and the other **Join with a code**.

## What it is

A bar jukebox is a one-person machine in a room full of people. Our Song gives two people who do not know each other a reason to choose a song together. It is built as a feature inside a jukebox app: free to play, and it ends with a paid play that two people chose.

1. **The room.** The entry screen is the bar tonight: who is here, your picture and your title by your name. Invite anyone.
2. **The deal.** Four cards, one of each suit. Stage is what you turn up, Bar is what you order, Road is your Saturday, Porch is you at home. Tap any card that is not you and Q-Lou swaps it. 101 cards, each one a verified Qloo signal.
3. **Three rounds.** One card each: most you, also you, a bit you. Drag it into the gold slot. Both cards turn over together and Q-Lou says one thing about the pair.
4. **The song.** The cards drift back to him. He finds the artist both of you score highest on and brings back the top track. Play it, two tokens each, or Not this one, and he brings one more, two picks at most.
5. **The end.** The song, who said yes, and what the venue knows about the two of you.

Partners, not competitors. Nobody wins over anybody. The song does.

## Where Qloo is

Every number in the game is Qloo's. Without Qloo there is no deck, no matrix, no pick.

| Step | What happens | Qloo | Code |
|---|---|---|---|
| Cards | Each card is one tag or audience, checked live before it was let into the deck | `/v2/tags`, `/v2/audiences` | `static/cards.json`, `probes/` |
| Matrix | All 101 cards scored against 2,003 artists that bars play, once, refreshed on a schedule; columns standardized | `/v2/insights` with `filter.results.entities` | `common_ground/matrix.py`, `static/matrix.json` |
| Draft | Three cards weigh 3, 2, 1; swapped cards pull against; each partner gets a curved score for every artist; the weaker score decides | local arithmetic over the matrix | `common_ground/qlou.py` |
| Verify | The top three re-scored live for both partners | `/v2/insights` | `common_ground/qlou.py` |
| Compare | What the two of you share | `/v2/analysis/compare` | `common_ground/qlou.py` |
| Control | A plain language model guesses from the same cards with no data; Qloo scores that guess too; both go on the ledger | `/search`, `/v2/insights` | `common_ground/qlou.py`, `common_ground/llm.py` |
| Track | The artist's top track | catalog lookup (iTunes Search in the demo, the jukebox's own catalog in production) | `common_ground/catalog.py` |

Qloo returns exactly 0.765 when it has no signal for an entity; that is treated as no data, never as agreement. Everything Q-Lou says is backed by a Qloo entity id or it is not said.

## Why it is agentic

`common_ground/qlou.py` is the agent. Q-Lou deals, swaps a card when asked, scores the hands, drafts the pick, verifies the top three live, re-picks when a partner says no, pairs people in the room, and seats a house regular when nobody is free. Every step streams to both phones while he works. The humans only choose cards and say yes or no.

## Layout

```
app.py                     FastAPI: pages and JSON routes
common_ground/
  qlou.py                  the agent
  matrix.py                the card by artist matrix, built from Qloo, kept current by a keeper thread
  qloo.py                  thin client for the Qloo Insights API
  rooms.py                 game state: rooms, sides, three rounds, tokens, votes
  venue.py                 the room tonight: presence, invites, house regulars
  deck.py                  the 101 cards in four suits
  quiz.py                  the house regulars for solo play
  stats.py                 one JSON line per game; venue memory (nights, titles, pictures)
  catalog.py               artist to top track (the seam to a real jukebox catalog)
  llm.py                   OpenRouter client with a daily cap, used for the control guess only
static/
  play.html                the whole game, one file, no framework
  quips.json               everything Q-Lou says, plain text
  screen.html              the venue's screen, read-only
  cards.json, matrix.json  the deck with its Qloo signals; the built matrix
  deck/, pics/             card art and profile pictures (generated, no people, no faces)
tests/                     55 tests, fake Qloo, no network
docs/                      background.md (the long write-up and research), the feel brief, the game design
probes/                    the scripts and logs that verified every card's signal
```

## Run it

```
pip install -r requirements.txt
cp .env.example .env        # QLOO_API_KEY required; OPENROUTER_API_KEY optional (the control guess)
uvicorn app:app --host 0.0.0.0 --port 8080
```

Open `/play` on a laptop or two phones. `/screen/<code>` shows one room on the venue's screen. Tests: `pip install -r requirements-dev.txt && python -m pytest -q`.

No cookies, no accounts. A room is a four-letter code and two nicknames. The venue remembers nicknames, pictures and nights at one venue in `data/`.

## What is real and what is not

Real: every Qloo call, the matrix, the pick, the control, the venue log, the two-phone play, the house regulars who sit in when you are alone. Not real: the jukebox does not actually play the song, tokens are play money, and the room pairs people by nickname at one venue rather than by the app's own pairing.

Known limits on the hackathon key as of October 2026: `feature.explainability` and `/v2/trending` return nothing; both are wired and would light up on a production key. Qloo files some multi-career celebrities as people rather than artists, so they are not in the pool. The long version of all of this, with sources, is in `docs/background.md`.

## Team

Brent Rafferty, with Claude as the builder. Built from scratch during the submission period, starting October 2, 2026.
