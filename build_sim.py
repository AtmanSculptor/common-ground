"""One-time: how similar are Qloo's audience segments to each other?
For each audience pull its top artists and movies; similarity = overlap. Writes common_ground/audience_sim.json."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from common_ground.qloo import Qloo
q = Qloo()
auds = q.all_audiences()
print(len(auds), "audiences")
tops = {}
for a in auds:
    ids = set()
    for t in ["urn:entity:artist", "urn:entity:movie"]:
        try:
            ids |= {e["entity_id"] for e in q.insights(t, audiences=[a["id"]], take=40, popularity_min=0.8)}
        except Exception as ex:
            print("err", a["id"], ex)
    tops[a["id"]] = ids
    print(a["id"].split(":")[-1], len(ids))
sim = {}
for x in tops:
    row = {}
    for y in tops:
        if x == y or not tops[x] or not tops[y]:
            continue
        inter = len(tops[x] & tops[y]); uni = len(tops[x] | tops[y])
        row[y] = round(inter / uni, 4) if uni else 0.0
    sim[x] = dict(sorted(row.items(), key=lambda kv: -kv[1])[:15])
json.dump({"audiences": auds, "sim": sim}, open("common_ground/audience_sim.json", "w", encoding="utf-8"), indent=0)
print("calls", q.calls)
for k in list(sim)[:5]:
    print(k.split(":")[-1], "->", [(j.split(":")[-1], v) for j, v in list(sim[k].items())[:4]])
