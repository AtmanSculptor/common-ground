"""Second pass: alternative signals for the weak cards. Prints top artists per option."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8"); sys.path.insert(0, ".")
from common_ground.qloo import Qloo
q = Qloo()
OPTS = {
 "Night owl": [{"tags":["urn:tag:genre:music:nightlife"]}, {"tags":["urn:tag:good_for:qloo:late_night"]}, {"audiences":["urn:audience:hobbies_and_interests:dance"]}],
 "Dog person": [{"audiences":["urn:audience:global_issues:animal_issues"]}],
 "Cat person": [{"audiences":["urn:audience:global_issues:animal_issues","urn:audience:leisure:avid_reader"]}],
 "Horror fan": [{"tags":["urn:tag:audience:qloo:horror_loving"]}, {"tags":["urn:tag:audience:qloo:horror_enthusiasts"]}],
 "Rom com": [{"tags":["urn:tag:audience:qloo:romantic_comedy_fans"]}],
 "Action junkie": [{"tags":["urn:tag:audience:qloo:action_loving"]}, {"audiences":["urn:audience:hobbies_and_interests:adrenaline_rushers"]}],
 "Sci fi": [{"tags":["urn:tag:audience:qloo:science_fiction_fans"]}, {"tags":["urn:tag:audience:qloo:science_fiction_enthusiasts"]}],
 "True crime": [{"tags":["urn:tag:audience:qloo:true_crime_fans"]}, {"audiences":["urn:audience:hobbies_and_interests:secret_unravelers"]}],
 "Comedy": [{"tags":["urn:tag:audience:qloo:comedy_loving"]}, {"tags":["urn:tag:genre:music:comedy"]}],
 "Reality TV": [{"tags":["urn:tag:audience:qloo:reality_tv_fans"]}],
 "Documentary": [{"tags":["urn:tag:audience:qloo:documentary_lovers"]}, {"audiences":["urn:audience:leisure:news_junkie"]}],
 "Cartoons": [{"tags":["urn:tag:audience:qloo:animation_fans"]}, {"tags":["urn:tag:genre:music:anime"]}],
 "Sports fan": [{"audiences":["urn:audience:hobbies_and_interests:baseball","urn:audience:hobbies_and_interests:american_football"]}],
 "Whiskey": [{"audiences":["urn:audience:spending_habits:gourmand_fine_dining"]}, {"tags":["urn:tag:genre:music:blues"]}],
 "Craft beer": [{"audiences":["urn:audience:leisure:foodie","urn:audience:leisure:music_festivals"]}],
 "Coffee snob": [{"audiences":["urn:audience:leisure:coffee","urn:audience:leisure:avid_reader"]}, {"tags":["urn:tag:genre:music:acoustic"]}],
 "Taco Tuesday": [{"tags":["urn:tag:genre:music:latin_pop"]}, {"audiences":["urn:audience:communities:latino"]}],
 "Pizza": [{"audiences":["urn:audience:leisure:foodie"]}, {"tags":["urn:tag:genre:music:pop_punk"]}],
 "Small town": [{"tags":["urn:tag:genre:music:americana"]}, {"tags":["urn:tag:genre:music:country"]}],
 "Big city": [{"tags":["urn:tag:genre:music:r_b"]}, {"audiences":["urn:audience:hobbies_and_interests:street_fashion"]}],
 "Science nerd": [{"tags":["urn:tag:genre:music:synthpop"]}, {"audiences":["urn:audience:spending_habits:technology_enthusiast","urn:audience:leisure:museums"]}],
 "Gamer": [{"tags":["urn:tag:genre:music:video_game_soundtrack"]}, {"audiences":["urn:audience:hobbies_and_interests:video_gamer"]}],
 "Live music": [{"audiences":["urn:audience:leisure:music_festivals","urn:audience:hobbies_and_interests:musician"]}, {"tags":["urn:tag:genre:music:jam_band"]}],
 "Early bird": [{"tags":["urn:tag:genre:music:acoustic"]}, {"audiences":["urn:audience:leisure:exercising","urn:audience:hobbies_and_interests:yoga"]}],
}
for n, opts in OPTS.items():
    print(f"\n## {n}")
    for o in opts:
        try: ents = q.insights("urn:entity:artist", take=5, popularity_min=0.9, **o)
        except Exception as e: ents=[]; print("  ERR", e)
        print(f"   {json.dumps(o)[:70]:70s} -> {', '.join(e['name'] for e in ents[:4])}")
print("calls", q.calls)
