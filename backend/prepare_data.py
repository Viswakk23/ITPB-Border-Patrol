import json
import math
import time
import urllib.request
from pathlib import Path

import osmnx as ox

import config

HERE = Path(__file__).resolve().parent
URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
       "master/geojson/ne_10m_admin_0_boundary_lines_land.geojson")
W, S, E, N = config.AREA

SERVERS = [
    "https://overpass.private.coffee/api",
    "https://overpass.private.coffee/api",
    "https://overpass.private.coffee/api",
    "https://overpass.private.coffee/api",
    "https://overpass.nchc.org.tw/api",
]
ROADS = '["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified"]'


def km(a, b):
    dlat = (b[0] - a[0]) * 111.0
    dlon = (b[1] - a[1]) * 111.0 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlon)


def use_server(base):
    # Different osmnx versions name this setting differently
    cur = getattr(ox.settings, "overpass_url", None) or getattr(ox.settings, "overpass_endpoint", "")
    new = base + "/interpreter" if str(cur).endswith("interpreter") else base
    if hasattr(ox.settings, "overpass_url"):
        ox.settings.overpass_url = new
    else:
        ox.settings.overpass_endpoint = new


# ---------- 1. The border line ----------
border_file = HERE / "border.json"
if border_file.exists():
    lines = json.loads(border_file.read_text(encoding="utf-8"))
    print("Step 1 of 3: border.json already exists, skipping.")
else:
    print("Step 1 of 3: downloading the border line...")
    with urllib.request.urlopen(URL, timeout=120) as r:
        data = json.load(r)
    lines = []
    for f in data["features"]:
        g = f.get("geometry")
        if not g or g["type"] not in ("LineString", "MultiLineString"):
            continue
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for part in parts:
            cur = []
            for lon, lat in part:
                if W <= lon <= E and S <= lat <= N:
                    cur.append([lat, lon])
                else:
                    if len(cur) > 1:
                        lines.append(cur)
                    cur = []
            if len(cur) > 1:
                lines.append(cur)
    if not lines:
        raise SystemExit("No border found in this area. Check AREA in config.py")
    border_file.write_text(json.dumps(lines), encoding="utf-8")
    print("  saved border.json with", len(lines), "line(s)")

# ---------- 2. The roads ----------
print("Step 2 of 3: downloading roads (5 to 15 minutes, please wait)...")
ox.settings.requests_timeout = 300
ox.settings.log_console = True      # shows progress lines while it works
ox.settings.overpass_rate_limit = False
ox.settings.max_query_area_size = 2 * 10**10   # shows progress lines while it works
G = None
for server in SERVERS:
    print("  trying server:", server)
    use_server(server)
    try:
        try:
            G = ox.graph_from_bbox((W, S, E, N), custom_filter=ROADS, simplify=False)
        except TypeError:
            G = ox.graph_from_bbox(N, S, E, W, custom_filter=ROADS, simplify=False)
        break
    except Exception as err:
        print("  this attempt failed:", type(err).__name__, "- waiting 30 seconds, then trying again")
        time.sleep(30)

if G is None:
    raise SystemExit("All servers failed. Try a phone hotspot and run again.")

G = ox.truncate.largest_component(G, strongly=True)
ox.save_graphml(G, HERE / "road_graph.graphml")
print("  saved road_graph.graphml with", len(G.nodes), "road points")

# ---------- 3. Simulated outposts near the border ----------
print("Step 3 of 3: choosing simulated outposts...")
pts = [p for line in lines for p in line]
coords = {n: (d["y"], d["x"]) for n, d in G.nodes(data=True)}

posts, used = [], set()
COUNT = 5
for i in range(COUNT):
    p = pts[int(i * (len(pts) - 1) / (COUNT - 1))]
    node = min(coords, key=lambda n: km(p, coords[n]))
    if node in used:
        continue
    used.add(node)
    c = coords[node]
    posts.append({
        "name": f"Outpost {len(posts) + 1} (simulated)",
        "lat": c[0], "lon": c[1],
        "border_km": round(min(km(c, q) for q in pts), 1),
    })

(HERE / "posts.json").write_text(json.dumps(posts), encoding="utf-8")
for p in posts:
    print("  ", p["name"], "-", p["border_km"], "km from the border")

base_node = min(coords, key=lambda n: km(config.BASE, coords[n]))
print("Base is", round(km(config.BASE, coords[base_node]), 1), "km from the nearest road point")
print("All done.")