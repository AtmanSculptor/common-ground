"""Rooms: the state of one game between two phones.

Phases (version 2, docs/game_v2.md):
  lobby     the host has a code; the partner has not joined yet
  toss      six cards each; toss up to three, fresh ones come back; lock in
  play      three cards on your side, in order: Most me, Also me, A bit me; lock in
  reveal    the pairs flip rank by rank; the meter moves and says why; Guilty or Objection
  thinking  Q-Lou is working (steps stream to both phones)
  jukebox   the pick and the question. Both yes: 2 tokens each and it plays. Any no: the next
            pick, up to three in all; after the third, no match tonight
  done      meter, title, what you share, the LLM-vs-Qloo control

State lives in memory and is mirrored to a pickle so a restart does not end a game.
No accounts, no cookies: a room is a code and two nicknames, swept after six hours.
"""
from __future__ import annotations

import pathlib
import pickle
import random
import threading
import time
from dataclasses import MISSING, dataclass, field, fields

from . import deck, matrix, quiz, stats
from .qlou import Partner, Pick, QLou
from .qloo import Qloo

PLAYS = 3                # cards each partner puts on the table, one per round, ranked
TOSS_MAX = 4             # at the deal you may swap any of your four
INTERLUDE_MIN = 12.0     # seconds the table holds after the third flip before the song can arrive: the meter, the words, the call
MAX_PICK_ATTEMPTS = 3    # three picks, then no match tonight
ROTATION = 25            # the last picks at this venue sit out of the draft, so a regular does not hear the same song twice
START_TOKENS = 10        # the demo balance; a jukebox app sets the real one
SONG_COST = 4
HALF = SONG_COST // 2
PLAYED_BONUS = 10
ROOM_TTL = 6 * 3600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"

TITLES = [(90, "Fully in tune"), (70, "Same page, different chapters"),
          (50, "Neighbors on the same street"), (30, "Different roads, same town"), (0, "Opposites, attracting")]
METER_GAIN = {"exact": 30, "kin": 12, "none": 4}    # per rank pair; three pairs and the played bonus reach 100
ROUNDS = ("deal", "r1", "r2", "r3")   # the deal, then one card per round: Most me, Also me, A bit me
RANK_NAMES = ("Most me", "Also me", "A bit me")


def make_code(rng: random.Random | None = None) -> str:
    rng = rng or random
    return "".join(rng.choice(CODE_ALPHABET) for _ in range(4))


HOUSE_TOSS_PAUSE = (3.0, 7.0)    # seconds a house partner takes to toss, like a person would
HOUSE_LOCK_PAUSE = (3.0, 7.0)    # seconds to put one card down, each round
HOUSE_VOTE_PAUSE = (2.0, 5.0)    # seconds to say yes to the song
EMOTES = ("cheers", "eyes", "fire", "laugh")   # the nudges a partner can send across the table
EMOTE_GAP = 1.5                  # seconds between two nudges from one phone
EMOTE_TTL = 8.0                  # how long a nudge stays in the state for the partner's next polls


@dataclass
class Side:
    name: str = ""
    joined: bool = False
    house: bool = False
    quiz: quiz.Quiz = field(default_factory=quiz.Quiz)   # a house partner's taste profile
    hand: list[int] = field(default_factory=list)        # four cards, one per suit
    icon: int = 0                                        # the profile mark this player picked (0..3)
    seen: list[int] = field(default_factory=list)
    tossed: list[int] = field(default_factory=list)      # round one: "not me"
    toss_locked: bool = False
    plays: list[int] = field(default_factory=list)       # one card per round, in rank order: Most me first
    placed: int = 0                    # kept for older callers; equals len(plays)
    tokens: int = START_TOKENS
    pending: list[int] | None = None   # a house partner's next move, waiting for its moment
    due: float = 0.0                   # when that moment is (epoch seconds)
    emote: dict | None = None          # the last nudge sent: {"name", "at"}

    @property
    def profile(self) -> list[str]:
        return self.quiz.profile

    @property
    def play_locked(self) -> bool:
        return len(self.plays) == PLAYS

    @property
    def joker_played(self) -> bool:
        return any(deck.card(c)["suit"] == deck.JOKER for c in self.plays)

    def partner(self, key: str) -> Partner:
        return Partner(key=key, name=self.name or key, cards=list(self.plays), profile=self.profile,
                       weights=list(matrix.RANK_WEIGHTS[:len(self.plays)]), tossed=list(self.tossed))


@dataclass
class Room:
    code: str
    created: float = field(default_factory=time.time)
    phase: str = "lobby"
    sides: dict[str, Side] = field(default_factory=lambda: {"host": Side(), "guest": Side()})
    toss_summary: dict | None = None
    rank: int = 0                                        # the round being played, 0..2
    pairs: list[dict] = field(default_factory=list)      # the pairs flipped so far, one per finished round
    revealed_at: float = 0.0                             # when the third pair flipped
    reveal: dict | None = None
    meter: int = 0
    thinking: list[dict] = field(default_factory=list)   # Q-Lou's live steps
    offers: list[dict] = field(default_factory=list)     # each pick: Pick.to_dict() + votes, attempt, status, paid_by
    excluded: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __getstate__(self):
        d = self.__dict__.copy(); d.pop("lock", None); return d

    def __setstate__(self, d):
        self.__dict__.update(d); self.lock = threading.Lock()

    # ---- the current offer ---------------------------------------------------------------
    @property
    def pick(self) -> dict | None:
        return self.offers[-1] if self.offers else None

    # ---- round one: toss -----------------------------------------------------------------
    def deal(self, rng: random.Random | None = None) -> None:
        rng = rng or random
        self.phase = "toss"
        for s in self.sides.values():
            s.hand = deck.deal(set(s.seen), rng)
            s.seen += s.hand
            if s.house:
                s.pending = self._house_toss(s, rng)
                s.due = time.time() + rng.uniform(*HOUSE_TOSS_PAUSE)
        self.log.append("dealt")

    def toss(self, who: str, cards: list[int], rng: random.Random | None = None) -> bool:
        """Toss up to three of your six; a fresh one of the same suit takes each place, so the hand
        keeps one of each. Tossing none is a choice too. Locks the deal for this side."""
        s = self.sides[who]
        if self.phase != "toss" or s.toss_locked:
            return False
        if any(c not in s.hand for c in cards):
            return False
        picks = [c for c in dict.fromkeys(cards) if deck.card(c)["suit"] != deck.JOKER]   # the joker stays: nobody tosses a free song
        if len(picks) > TOSS_MAX:
            return False
        for c in picks:
            new = deck.draw_one(set(s.seen), c, rng)
            if new is None:
                continue
            s.hand[s.hand.index(c)] = new
            s.seen.append(new)
            s.tossed.append(c)
        s.toss_locked = True
        s.placed = 0
        self.log.append(f"{s.name or who} tossed {len(s.tossed)}")
        if all(x.toss_locked for x in self.sides.values()):
            self._open_play(rng)
        return True

    def _open_play(self, rng: random.Random | None = None) -> None:
        rng = rng or random
        h, g = self.sides["host"], self.sides["guest"]
        both = sorted({deck.card(c)["name"] for c in set(h.tossed) & set(g.tossed)})
        self.toss_summary = {"host": [deck.card(c)["name"] for c in h.tossed], "guest": [deck.card(c)["name"] for c in g.tossed], "both": both}
        self.phase = "play"
        for s in self.sides.values():
            s.placed = 0
            if s.house:
                s.pending = self._house_plays(s, rng)
                s.due = time.time() + rng.uniform(*HOUSE_LOCK_PAUSE)

    # ---- the rounds: one card each, then the pair flips ----------------------------------
    def play(self, who: str, card: int, rng: random.Random | None = None) -> bool:
        """One card from the hand for the round being played. When both are down the pair flips
        and the next round opens; after the third flip the table goes to Q-Lou by itself."""
        s = self.sides[who]
        if self.phase != "play" or self.down(who) or card not in s.hand or card in s.plays:
            return False
        s.plays.append(card)
        s.placed = len(s.plays)
        self.log.append(f"{s.name or who} put down {deck.card(card)['name']} ({RANK_NAMES[self.rank]})")
        if all(self.down(k) for k in self.sides):
            self._flip(rng)
        return True

    def down(self, who: str) -> bool:
        """This side has its card down for the round being played."""
        return len(self.sides[who].plays) > self.rank

    def _flip(self, rng: random.Random | None = None) -> None:
        h, g = self.sides["host"], self.sides["guest"]
        ca, cb = deck.card(h.plays[self.rank]), deck.card(g.plays[self.rank])
        self.pairs.append({"rank": self.rank + 1, "a": deck.public(ca), "b": deck.public(cb),
                           "match": deck.match_kind(ca, cb), "at": time.time()})
        self.log.append(f"flip {self.rank + 1}: {ca['name']} and {cb['name']}, {self.pairs[-1]['match']}")
        self.rank += 1
        if self.rank >= PLAYS:
            self.do_reveal(rng)
            return
        for s in self.sides.values():        # the house takes a moment before its next card
            if s.house and s.pending:
                s.due = time.time() + (rng or random).uniform(*HOUSE_LOCK_PAUSE)

    def both_locked(self) -> bool:
        return all(s.play_locked for s in self.sides.values())

    # ---- after the third flip: the meter, the words, and Q-Lou leaves to think ------------
    def do_reveal(self, rng: random.Random | None = None) -> None:
        """The meter counts the pairs; a card both played anywhere is a same card for the words
        even if it sits at different ranks. The phase moves to thinking: nobody presses a button."""
        rng = rng or random
        h, g = self.sides["host"], self.sides["guest"]
        pairs = [{k: v for k, v in p.items() if k != "at"} for p in self.pairs]
        scored = self._score_pairs(h.plays, g.plays)
        gained = sum(METER_GAIN[p["match"]] for p in scored)
        both = [p["a"]["name"] for p in scored if p["match"] == "exact"]
        self.meter = min(100, self.meter + gained)
        counts = {k: sum(1 for p in scored if p["match"] == m) for k, m in (("same", "exact"), ("close", "kin"), ("apart", "none"))}
        self.reveal = {"pairs": pairs, "both": both, "meter": self.meter, "words": deck.meter_words(scored, both),
                       "counts": counts, "pull": self._pull()}
        self.revealed_at = time.time()
        self.phase = "thinking"
        self.log.append(f"revealed, meter {self.meter}")

    @staticmethod
    def _score_pairs(hp: list[int], gp: list[int]) -> list[dict]:
        """The pairing the meter counts: a card both played is a same card wherever it sits, then
        the closest of what is left face each other, then the rest in order."""
        h, g = list(hp), list(gp)
        out = []
        for c in list(h):
            if c in g:
                out.append({"a": deck.public(deck.card(c)), "b": deck.public(deck.card(c)), "match": "exact"})
                h.remove(c); g.remove(c)
        for hc in list(h):
            for gc in list(g):
                if deck.match_kind(deck.card(hc), deck.card(gc)) == "kin":
                    out.append({"a": deck.public(deck.card(hc)), "b": deck.public(deck.card(gc)), "match": "kin"})
                    h.remove(hc); g.remove(gc)
                    break
        out += [{"a": deck.public(deck.card(hc)), "b": deck.public(deck.card(gc)), "match": "none"} for hc, gc in zip(h, g)]
        return out

    def _pull(self) -> dict | None:
        """The two cards, one from each side, pulling the same way: the pair whose Qloo columns agree
        most across the pool. What Q-Lou names before he leaves to think."""
        m = matrix.load()
        h, g = self.sides["host"], self.sides["guest"]
        if not m or not h.plays or not g.plays:
            return None
        best, best_v = None, -1.0
        for hc in h.plays:
            ca = m["scores"].get(str(hc), {})
            for gc in g.plays:
                cb = m["scores"].get(str(gc), {})
                common = [e for e in ca if e in cb]
                if len(common) < 20:
                    continue
                xs, ys = [ca[e] for e in common], [cb[e] for e in common]
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                sxx = sum((x - mx) ** 2 for x in xs) ** 0.5 * sum((y - my) ** 2 for y in ys) ** 0.5
                v = sxy / sxx if sxx else 0.0
                if v > best_v:
                    best, best_v = (hc, gc), v
        if not best:
            return None
        return {"host": deck.card(best[0])["name"], "guest": deck.card(best[1])["name"], "agreement": round(best_v, 2)}

    # ---- the house, on a human clock -----------------------------------------------------
    def tick(self, rng: random.Random | None = None, now: float | None = None) -> str | None:
        """A house partner acts when its moment comes, not the instant the human does. Called on
        every poll. Returns what it did: 'play' (its toss opened the rounds), 'down' (its card is
        down, waiting on the human), 'flip' (its card completed a pair), 'reveal' (the third pair:
        Q-Lou's turn), 'vote', or None."""
        rng = rng or random
        now = time.time() if now is None else now
        for key, s in self.sides.items():
            if not s.house or now < s.due or s.pending is None:
                continue
            if self.phase == "toss" and not s.toss_locked:
                cards, s.pending = s.pending, None
                self.toss(key, cards, rng)
                return "play" if self.phase == "play" else "toss"
            if self.phase == "play" and not self.down(key) and s.pending:
                before = self.rank
                card = s.pending.pop(0)
                if not s.pending:
                    s.pending = None
                self.play(key, card, rng)
                if self.phase == "thinking":
                    return "reveal"
                return "flip" if self.rank > before else "down"
            if self.phase == "jukebox" and self.pick and s.pending == []:
                s.pending = None
                self.vote(key, True)      # the house always says yes to a shared song
                return "vote"
        return None

    def house_will_vote(self, rng: random.Random | None = None) -> None:
        """The human voted; the house answers in a few seconds."""
        g = self.sides["guest"]
        if g.house and self.pick and "guest" not in self.pick["votes"]:
            g.pending, g.due = [], time.time() + (rng or random).uniform(*HOUSE_VOTE_PAUSE)

    @staticmethod
    def _house_score(s: Side, cid: int, rng: random.Random) -> float:
        fams = {quiz.family(a) for a in s.profile}
        sig = deck.card(cid)["signal"]
        return sum(1 for a in sig.get("audiences", []) if quiz.family(a) in fams) + rng.random() * 0.5

    def _house_toss(self, s: Side, rng: random.Random | None = None) -> list[int]:
        """The house swaps the cards furthest from its profile, up to three of four, sometimes none."""
        rng = rng or random
        scored = sorted(((self._house_score(s, c, rng), c) for c in s.hand if deck.card(c)["suit"] != deck.JOKER))
        n = rng.choice((0, 1, 1, 2, 2, 3))
        return [c for _, c in scored[:n]]

    def _house_plays(self, s: Side, rng: random.Random | None = None) -> list[int]:
        """The three cards closest to its profile, best first."""
        rng = rng or random
        scored = sorted(((self._house_score(s, c, rng), c) for c in s.hand), reverse=True)
        return [c for _, c in scored[:PLAYS]]

    def emote(self, who: str, name: str, now: float | None = None) -> bool:
        """A nudge across the table. Rate limited; the partner's polls carry it for a few seconds."""
        now = time.time() if now is None else now
        if name not in EMOTES:
            return False
        s = self.sides[who]
        if s.emote and now - s.emote["at"] < EMOTE_GAP:
            return False
        s.emote = {"name": name, "at": now}
        return True

    # ---- Q-Lou ---------------------------------------------------------------------------
    def think(self, q: Qloo, *, use_llm: bool = True, recent: list[str] = ()) -> Pick | None:
        """Run Q-Lou. Steps stream into self.thinking as they happen. The caller moves the phase:
        the pick may be ready while the phones are still watching the reveal. 'recent' is the
        venue's rotation: artists picked lately sit this one out."""
        attempt = len(self.offers) + 1
        self.thinking = []

        def refresh(late: Pick) -> None:
            """A slow extra (the tracks, the control) landed after the pick was returned."""
            offer = next((o for o in self.offers if o.get("attempt") == attempt), None)
            if offer:
                d = late.to_dict()
                offer.update({k: d[k] for k in ("tracks", "why", "why_source", "baseline", "live", "shared", "calls")})

        agent = QLou(q, use_llm=use_llm, on_step=self.thinking.append, on_update=refresh)
        p = agent.pick(self.sides["host"].partner("host"), self.sides["guest"].partner("guest"), set(self.excluded) | set(recent))
        if len(self.offers) >= attempt:
            return None   # something else filled this attempt meanwhile; this run is stale
        if p is None:
            self.log.append(f"Q-Lou came up empty on attempt {attempt}")
            return None
        self.offers.append({**p.to_dict(), "votes": {}, "attempt": attempt, "status": "open", "paid_by": None, "played": None})
        self.log.append(f"pick {attempt}: {p.artist['name']} in {p.seconds}s, {p.calls} Qloo calls")
        return p

    # ---- the song and the tokens ---------------------------------------------------------
    def vote(self, who: str, up: bool) -> None:
        if self.pick and self.pick["status"] == "open":
            self.pick["votes"][who] = bool(up)

    def settled(self) -> str | None:
        """Both votes are in on the open offer: 'play' (both yes, 2 tokens each), 'again' (someone
        said no and Q-Lou has another in him), 'nothing' (three picks, no match tonight), or None
        while waiting. A yes from someone who cannot pay their half counts as a no."""
        o = self.pick
        if not o or o["status"] != "open" or len(o["votes"]) < 2:
            return None
        on_the_house = any(s.joker_played for s in self.sides.values())
        can_pay = {k: on_the_house or s.tokens >= HALF for k, s in self.sides.items()}
        if all(o["votes"].values()) and all(can_pay.values()):
            if not on_the_house:
                for s in self.sides.values():
                    s.tokens -= HALF
            o.update({"status": "played", "played": True, "paid_by": "house" if on_the_house else "both"})
            return "play"
        o.update({"status": "declined", "played": False})
        self.excluded.append(o["artist"]["id"])
        return "again" if o["attempt"] < MAX_PICK_ATTEMPTS else "nothing"

    @property
    def played_offer(self) -> dict | None:
        return next((o for o in self.offers if o["status"] == "played"), None)

    # ---- result --------------------------------------------------------------------------
    def finish(self) -> dict:
        """The meter keeps the meaning it had on the table (what the cards had in common, plus a
        little for a song that got played). The song's own fit is shown beside it, not folded in."""
        played = self.played_offer
        if played:
            self.meter = min(100, self.meter + PLAYED_BONUS)
        title = next(t for m, t in TITLES if self.meter >= m)
        names = {k: (s.name or k) for k, s in self.sides.items()}
        pairs = (self.reveal or {}).get("pairs", [])
        self.result = {
            "meter": self.meter, "title": title,
            "words": (self.reveal or {}).get("words", ""),
            "counts": (self.reveal or {}).get("counts"),
            "plays": {names[k]: [deck.card(c)["name"] for c in s.plays] for k, s in self.sides.items()},
            "tossed": {names[k]: [deck.card(c)["name"] for c in s.tossed] for k, s in self.sides.items()},
            "both_played": sorted({deck.card(c)["name"] for c in set(self.sides["host"].plays) & set(self.sides["guest"].plays)}),
            "tokens": {k: s.tokens for k, s in self.sides.items()},
            "on_the_house": any(s.joker_played for s in self.sides.values()),
            "paid_by": played["paid_by"] if played else None,
            "offers": [{"artist": o.get("artist"), "status": o["status"], "attempt": o["attempt"]} for o in self.offers],
            "pick": self._result_pick(),
        }
        self.phase = "done"
        return self.result

    RESULT_PICK_KEYS = ("artist", "tracks", "played", "paid_by", "why", "why_source", "ranked", "baseline", "explain", "scores",
                        "live", "shared", "attempt", "calls", "seconds")

    def _result_pick(self) -> dict | None:
        """The song that played, else the last offer, as the done screen shows it. Read fresh each
        time: the control experiment may land late."""
        o = self.played_offer or self.pick
        return {k: o.get(k) for k in self.RESULT_PICK_KEYS} if o else None

    # ---- what a phone sees ---------------------------------------------------------------
    def public(self, who: str | None = None) -> dict:
        now = time.time()
        sides = {}
        for k, s in self.sides.items():
            locked = s.toss_locked if self.phase == "toss" else s.play_locked
            d = {"name": s.name, "joined": s.joined, "house": s.house, "locked": locked, "icon": getattr(s, "icon", 0),
                 "placed": len(s.plays), "down": len(s.plays) > self.rank, "tokens": s.tokens, "tossed_n": len(s.tossed), "hand_n": len(s.hand),
                 "profile": stats.profile(s.name) if s.joined else None,
                 "emote": s.emote if s.emote and now - s.emote["at"] < EMOTE_TTL else None}
            if k == who and s.hand:
                d["hand"] = [deck.public(deck.card(c)) for c in s.hand]
                d["plays"] = list(s.plays)
                d["tossed"] = list(s.tossed)
            sides[k] = d
        offers = []
        for o in self.offers:
            pub = {k: v for k, v in o.items() if k not in ("votes", "steps")}
            pub["voted"] = {k: (k in o["votes"]) for k in self.sides}
            pub["said_no"] = [k for k, v in o["votes"].items() if not v] if o["status"] != "open" else []
            offers.append(pub)
        return {"code": self.code, "phase": self.phase, "sides": sides, "rounds": list(ROUNDS), "rank": self.rank,
                "rank_names": list(RANK_NAMES), "pairs": list(self.pairs),
                "cost": SONG_COST, "half": HALF, "toss_max": TOSS_MAX, "plays": PLAYS, "tries": MAX_PICK_ATTEMPTS,
                "on_the_house": any(s.joker_played for s in self.sides.values()), "suits": list(deck.SUITS),
                "toss_summary": self.toss_summary if self.phase != "toss" else None,
                "reveal": self.reveal if self.phase in ("thinking", "jukebox", "done") else None,
                "meter": self.meter, "thinking": self.thinking[-8:], "pick": offers[-1] if offers else None, "offers": offers,
                "log": self.log[-5:],
                "result": {**self.result, "pick": self._result_pick()} if self.result else None, "t": now}


class Rooms:
    def __init__(self, path: str | pathlib.Path | None = None, rng: random.Random | None = None) -> None:
        self.path = pathlib.Path(path) if path else pathlib.Path(__file__).resolve().parents[1] / "data" / "rooms.pkl"
        self.rooms: dict[str, Room] = {}
        self.lock = threading.Lock()
        self.rng = rng or random.Random()
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                self.rooms = pickle.loads(self.path.read_bytes())
        except Exception:
            self.rooms = {}
        keep = {}
        for code, r in self.rooms.items():          # rooms pickled before a field existed get its default
            if "offers" not in r.__dict__ or "pairs" not in r.__dict__:
                continue                             # a room from before the rounds: let it go
            for f in fields(Room):
                if f.name not in r.__dict__ and f.name != "lock":
                    r.__dict__[f.name] = f.default_factory() if f.default_factory is not MISSING else f.default  # type: ignore[misc]
            for side in r.sides.values():
                for f in fields(Side):
                    if f.name not in side.__dict__:
                        side.__dict__[f.name] = f.default_factory() if f.default_factory is not MISSING else f.default  # type: ignore[misc]
            keep[code] = r
        self.rooms = keep

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(self.rooms))
            tmp.replace(self.path)
        except Exception:
            pass

    def create(self, host_name: str, house: bool = False, icon: int = 0) -> Room:
        with self.lock:
            code = make_code(self.rng)
            while code in self.rooms:
                code = make_code(self.rng)
            r = Room(code)
            r.sides["host"].name, r.sides["host"].joined = host_name, True
            r.sides["host"].icon = int(icon) % 4
            if house:
                h = self.rng.choice(quiz.HOUSE)
                g = r.sides["guest"]
                g.name, g.joined, g.house = h["name"], True, True
                g.quiz = quiz.Quiz.preset(h["profile"])      # the house keeps a taste profile; it drives which cards it plays
                g.icon = (sum(map(ord, h["name"])) % 3) + 1   # a house regular has its own mark
                r.log.append(f"Q-Lou seats you with {h['name']}")
                r.deal(self.rng)
            self.rooms[code] = r
            self.save()
            return r

    def get(self, code: str) -> Room | None:
        return self.rooms.get((code or "").upper())

    def join(self, code: str, name: str, icon: int = 0) -> Room | None:
        r = self.get(code)
        if not r or r.sides["guest"].joined:
            return None
        r.sides["guest"].name, r.sides["guest"].joined = name, True
        r.sides["guest"].icon = int(icon) % 4
        with r.lock:
            if r.phase == "lobby":
                r.deal(self.rng)
        self.save()
        return r

    def sweep(self, max_age: float = ROOM_TTL) -> int:
        now = time.time()
        with self.lock:
            old = [k for k, r in self.rooms.items() if now - r.created > max_age]
            for k in old:
                del self.rooms[k]
        return len(old)
