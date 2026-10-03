"""Run the Common Ground agent once and print the brief, the picks, and the LLM-guess check."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from common_ground.engine import Group
from common_ground.agent import run

P = "urn:audience:political_preferences:"
groups = [Group("progressives", [P + "politically_progressive"]),
          Group("conservatives", [P + "politically_conservative"])]
goal = sys.argv[1] if len(sys.argv) > 1 else "plan a community movie-and-music night that both sides will actually come to"
location = sys.argv[2] if len(sys.argv) > 2 else None

out = run(groups, goal, location)
json.dump(out, open("agent_out.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)

print(f"\n== {out['brief'].get('title')}  ({out['qloo_calls']} Qloo calls, {out['seconds']}s)\n")
print(out["brief"].get("brief"))
print("\nPICKS:")
for p in out["brief"].get("picks", []):
    print(f"   [{p.get('domain')}] {p.get('name')}: {p.get('why')}")
print("\nAVOID:")
for a in out["brief"].get("avoid", []):
    print(f"   {a.get('name')}: {a.get('why')}")
print("\nPLAIN LLM GUESSES, CHECKED AGAINST THE DATA:")
for d, r in out["results"].items():
    for c in r["llm_guesses_checked"]:
        if c.get("found"):
            print(f"   [{d}] {c['guess']:35s} {c.get('verdict', '?'):8s} {c['raw']}")
        else:
            print(f"   [{d}] {c['guess']:35s} not in Qloo")
print("\nTRACE:")
for s in out["trace"]:
    print("  ", {k: v for k, v in s.items() if k not in ('t', 'guesses')})
