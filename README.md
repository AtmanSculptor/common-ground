# Common Ground

**What do you both love? Measured, not guessed.**

Common Ground is an agent that finds what two people, or two groups, actually share, using Qloo's taste data instead of stereotypes, and then does something with it: deals a game, picks the jukebox song, or writes the plan for a night everyone will show up to.

Built for the Qloo Agentic Hackathon 2026.

## Two ways to play

**The game.** Two phones, one room code. Each person answers ten quick "which is more you?" questions. The agent turns those answers into a real Qloo audience profile, builds a deck of cards both profiles rate highly (a movie, a show, an artist), and both people rate each card. Then the jukebox pick: one artist the data says you both love. Both thumbs up and it plays. Score, a title, a note about what you share, and a playlist. Playing alone? Play the house: five characters with real Qloo profiles, shown on screen.

**The organizer.** Pick groups from 104 Qloo audience segments (progressives and conservatives, young parents and retirees, Christians and the LGBTQ community, foodies and discount shoppers, any mix). Add a town and a goal. The agent finds what unites them and what divides them across films, music, podcasts, books, places and brands, writes a plan citing only entities Qloo returned, and shows a plain LLM's guess next to the data, scored, so you can see where the guess was wrong.

## Why Qloo makes this work

A plain LLM asked "what do progressives and conservatives both love?" answers from stereotypes. We checked: it guessed Johnny Cash, The Beatles, Stevie Wonder, Taylor Swift and Top Gun: Maverick. Qloo's audience affinities say every one of those splits the two groups. Dolly Parton, Willie Nelson, Norah Jones, Ghostbusters and Black Panther: Wakanda Forever hold. Only measured taste data can tell those apart, and that difference is the product.

## How the agent uses Qloo

- `/v2/audiences` and `/v2/audiences/types`: the 104 segments people pick from, and the raw material of the quiz.
- `/v2/insights` with `signal.demographics.audiences`: each group's own affinities, plus the combined-audience call for candidates.
- `/v2/insights` with `filter.results.entities`: the verification step. Every candidate is scored against every group separately. Something is common ground only if the group that likes it least still rates it near the top.
- `/search`: resolving names (a favorite, or the LLM's guesses) to Qloo entity ids so they can be scored.
- `signal.interests.entities`: a named favorite as a signal, for the "one thing you love" sharpener.
- Entity tags (genre, style, theme) for the why under each pick.
- A one-time audience similarity map (top artists and films per segment, overlap) drives the adaptive quiz: each next question is the segment most related to what you've chosen so far, against a wildcard from a part of the map you haven't touched.

Qloo returns exactly 0.765 when it has no audience signal for an entity. The agent treats that as no data, never as agreement.

## The method, in one paragraph

Top-N lists per group never overlap; every group's favorites are its own long tail. So the engine builds a candidate pool (each group's list plus the combined-audience call, with a popularity floor), scores every candidate against every group one at a time, rescales within each group (raw affinities sit near 1.0 and some groups skew high), and ranks by the weakest group's score. The spread between groups is the "divides" score. The same engine serves two groups or six, audiences or people.

## Run it

```
pip install -r requirements.txt
cp .env.example .env   # add QLOO_API_KEY and OPENROUTER_API_KEY
uvicorn app:app --host 0.0.0.0 --port 8080
```

Open `/game` for the game, `/` for the organizer. No accounts, no cookies, no personal data stored: a room is a code, two names, ten taps each and some star ratings, swept after six hours.

## Stack

Python, FastAPI, plain HTML and JS, Qloo Insights API, OpenRouter (Gemini) for the planner, the baseline guess and the written briefs. One process, one file of state.

## Honest limits

Small towns are thin on the place side of Qloo's data (Bisbee, Arizona returns nothing; Tucson is fine). Books skew toward the long tail. The house characters are hand-written profiles, not real people. The LLM writes prose only from entities the agent hands it, and any pick it names that Qloo did not return is dropped before display.

## License

MIT.
