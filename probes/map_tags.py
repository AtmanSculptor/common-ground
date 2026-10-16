"""For each of the 54 cards, list candidate Qloo tags (search) and audiences so BT can pick exact ids."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8"); sys.path.insert(0, ".")
from common_ground.qloo import Qloo
q = Qloo()
cards = json.load(open("static/cards.json"))
QUERIES = {
 "Early bird":["morning","coffee"],"Night owl":["nightlife","late night"],"Road trips":["road trip","travel"],
 "Live music":["live music","concert"],"Dog person":["dogs","dog"],"Cat person":["cats","cat"],
 "Home cook":["cooking","home cooking"],"Takeout night":["takeout","fast food"],"Gym rat":["fitness","gym"],
 "Trail head":["hiking","outdoors"],"Bookworm":["books","reading"],"Gamer":["video games","gaming"],
 "Fix it myself":["diy","home improvement"],"Thrift find":["thrift","vintage"],"Big spender":["luxury"],
 "Saver":["budget","frugal"],"Spiritual":["spirituality","meditation"],"Science nerd":["science"],
 "Country roads":["country music","country"],"Hip hop head":["hip hop","rap"],"Rock and roll":["rock"],
 "Pop star":["pop"],"Jazz hands":["jazz"],"Metal":["metal","heavy metal"],"Indie kid":["indie","indie rock"],
 "EDM":["electronic","edm"],"Oldies":["oldies","classic rock"],"Latin heat":["latin","latin music"],
 "Gospel":["gospel","christian"],"Singer songwriter":["singer-songwriter","folk"],
 "Horror fan":["horror"],"Rom com":["romantic comedy","romance"],"Action junkie":["action"],"Sci fi":["science fiction","sci-fi"],
 "True crime":["true crime"],"Comedy":["comedy","stand-up"],"Sports fan":["sports"],"Reality TV":["reality"],
 "Documentary":["documentary"],"Cartoons":["animation","cartoon"],"Whiskey":["whiskey"],"Craft beer":["craft beer","beer"],
 "Wine night":["wine"],"Coffee snob":["coffee"],"Taco Tuesday":["mexican","tacos"],"Pizza":["pizza"],
 "Small town":["small town","rural"],"Big city":["city","urban"],"Family first":["family","parents"],
 "Single and fine":["single"],"Old soul":["nostalgia","classic"],"Wanderlust":["travel","adventure"],
 "Homebody":["cozy","home"],"Volunteer":["volunteering","community"],
}
out = {}
for c in cards:
    n = c["name"]; cands = {}
    for qq in QUERIES.get(n, [n.lower()]):
        for t in q.tags(qq, take=12):
            tid = t.get("id"); 
            if tid and tid not in cands: cands[tid] = t.get("name")
    out[n] = cands
    print(f"\n## {n}")
    for tid, tn in list(cands.items())[:14]: print(f"   {tid}  |  {tn}")
json.dump(out, open("tag_candidates.json","w"), indent=1)
print("\nAUDIENCES:")
for a in q.all_audiences(): print(f"   {a['id']}  |  {a['name']}")
print("calls", q.calls)
