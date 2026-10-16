import sys; sys.stdout.reconfigure(encoding="utf-8"); sys.path.insert(0, ".")
from common_ground.qloo import Qloo
q = Qloo()
DEAD = {"The Intruders","Case","Snow White's Poison Bite","Mashrou' Leila"}
for t in ["urn:tag:genre:music:musica_latina","urn:tag:genre:music:latino","urn:tag:genre:music:reggaeton","urn:tag:genre:music:latin_rock",
          "urn:tag:genre:music:rnb","urn:tag:genre:music:soul","urn:tag:genre:music:house","urn:tag:genre:music:neo_soul","urn:tag:genre:music:chiptune"]:
    ents = q.insights("urn:entity:artist", take=5, popularity_min=0.9, tags=[t])
    names = [e["name"] for e in ents[:4]]
    print(f"{'DEAD' if set(names)&DEAD else 'ok  '} {t:45s} {', '.join(names)}")
print("calls", q.calls)
