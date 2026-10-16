"""1) every card's signal returns real artists; 2) rerun pairs with the engine curve."""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8"); sys.path.insert(0, ".")
from common_ground.qloo import Qloo, affinity
q = Qloo()
cards = {c["name"]: c for c in json.load(open("static/cards.json"))}


def sig(names):
    out = {"tags": [], "audiences": []}
    for n in names:
        for k, v in cards[n]["signal"].items(): out[k] += v
    return {k: v for k, v in out.items() if v}

def pool(s, take=25):
    return {e["entity_id"]: e for e in q.insights("urn:entity:artist", take=take, popularity_min=0.9, **s)}

def score(ids, s):
    out = {}
    for i in range(0, len(ids), 50):
        b = ids[i:i+50]
        for e in q.insights("urn:entity:artist", result_entities=b, take=len(b), **s): out[e["entity_id"]] = affinity(e)
    return out

def curve(d):
    lo, hi = min(d.values()), max(d.values())
    return {k: (v-lo)/(hi-lo) if hi-lo > 1e-9 else 1.0 for k, v in d.items()}

def cg(a, b, label):
    sa, sb = sig(a), sig(b)
    both = {"tags": sa.get("tags",[])+sb.get("tags",[]), "audiences": sa.get("audiences",[])+sb.get("audiences",[])}
    both = {k:v for k,v in both.items() if v}
    p = {**pool(sa), **pool(sb), **pool(both, 40)}
    ids = list(p)
    ra, rb = score(ids, sa), score(ids, sb)
    keep = [i for i in ids if i in ra and i in rb and abs(ra[i]-0.765)>1e-6 and abs(rb[i]-0.765)>1e-6]
    ca, cb = curve({i: ra[i] for i in keep}), curve({i: rb[i] for i in keep})
    rows = sorted(((min(ca[i],cb[i]), ca[i], cb[i], p[i]["name"]) for i in keep), reverse=True)
    print(f"\n== {label}: A={a} B={b}  pool={len(ids)} kept={len(keep)}")
    for r in rows[:6]: print(f"   both={r[0]:.2f} A={r[1]:.2f} B={r[2]:.2f}  {r[3]}")
    return [r[3] for r in rows[:5]]

t0 = time.time()
print("== PAIRS")
r1 = cg(["Country roads","Dog person"], ["Hip hop head","Night owl"], "RUN 1")
r2 = cg(["Country roads","Dog person"], ["Rock and roll","Night owl"], "RUN 2 swap hip hop->rock")
r3 = cg(["Latin heat","Taco Tuesday"], ["Pop star","Big city"], "RUN 6 latin vs pop/city")
r4 = cg(["Whiskey","Small town"], ["Craft beer","Live music"], "RUN 7 bar people")
r5 = cg(["Oldies","Old soul"], ["Indie kid","Coffee snob"], "RUN 8 generations")
print(f"\n== overlap r1/r2 {len(set(r1)&set(r2))}/5  calls={q.calls} {time.time()-t0:.0f}s")
