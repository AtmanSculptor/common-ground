"""Smoke run: score a few group pairs across entity types and print what unites and divides."""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from common_ground.qloo import Qloo
from common_ground.engine import Group, score_groups, summarize

q = Qloo()
P = "urn:audience:political_preferences:"
L = "urn:audience:life_stage:"
pairs = {
    "prog_vs_cons": [Group("progressive", [P + "politically_progressive"]), Group("conservative", [P + "politically_conservative"])],
    "three_way": [Group("progressive", [P + "politically_progressive"]), Group("center", [P + "politically_center"]), Group("conservative", [P + "politically_conservative"])],
    "parents_vs_retired": [Group("young parents", [L + "parents_with_young_children"]), Group("retired", [L + "retirement"])],
}
types = sys.argv[1:] or ["urn:entity:artist", "urn:entity:movie", "urn:entity:podcast"]
report = {}
for name, groups in pairs.items():
    for t in types:
        t0 = time.time(); c0 = q.calls
        scored = score_groups(q, groups, t)
        s = summarize(scored, top=8)
        report[f"{name}|{t}"] = s
        print(f"\n===== {name} | {t}  pool={s['pool_size']}  calls={q.calls - c0}  {time.time() - t0:.1f}s")
        print("UNITES:")
        for x in s["unites"]:
            print(f"   {x['entity']['name']:40s} common={x['common']:.2f} divide={x['divide']:.2f} raw={x['raw']}")
        print("DIVIDES:")
        for x in s["divides"][:6]:
            print(f"   {x['entity']['name']:40s} leans={x['leans']:12s} divide={x['divide']:.2f} raw={x['raw']}")
json.dump(report, open("engine_out.json", "w"), indent=1)
print("\ntotal calls", q.calls)
