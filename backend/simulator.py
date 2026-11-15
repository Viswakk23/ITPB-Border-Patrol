import json
import math
import random
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import networkx as nx
import osmnx as ox
from scipy.spatial import cKDTree

import config
from database import SessionLocal
from models import Alert, Dispatch, Maint, Notice, Vehicle
from sensors import WEAR_BY_VEHICLE

HERE = Path(__file__).resolve().parent
SPEEDUP = 30            # time runs 30x faster so you can watch (use 1 for real speed)
TICK = 0.25             # vehicles move 4 times a second
PLAN_EVERY = 1.0        # alerts are planned once a second
ONSCENE_SECONDS = 25    # how long a team stays at the alert
DEFAULT_WEAR = 0.08     # health points lost per km (each vehicle has its own, see sensors.py)
W, S, E, N = config.AREA

# The team that each alert level needs
TEAMS = {
    "LOW": ["Patrol Jeep", "Recon Drone"],
    "MEDIUM": ["Quick Reaction Vehicle", "Patrol Jeep", "Recon Drone"],
    "HIGH": ["Armoured APC", "Quick Reaction Vehicle", "Emergency Vehicle", "Recon Drone"],
    "CRITICAL": ["Armoured APC", "Armoured APC", "Quick Reaction Vehicle",
                 "Emergency Vehicle", "Helicopter", "Recon Drone"],
}
RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}          # higher = more urgent
MAX_ETA = {"LOW": 240, "MEDIUM": 120, "HIGH": 75, "CRITICAL": 45}  # minutes. Slower = ask for help

TROOPS = {"Patrol Jeep": 4, "Quick Reaction Vehicle": 8, "Armoured APC": 12,
          "Emergency Vehicle": 3, "Helicopter": 6, "Recon Drone": 0}
KMH = {"Patrol Jeep": 60, "Quick Reaction Vehicle": 75, "Armoured APC": 45,
       "Emergency Vehicle": 80, "Recon Drone": 90, "Helicopter": 250}
BURN = {"Patrol Jeep": 0.25, "Quick Reaction Vehicle": 0.30, "Armoured APC": 0.50,
        "Emergency Vehicle": 0.25, "Recon Drone": 0.30, "Helicopter": 0.08}   # fuel % per km
PATROLLERS = ("Patrol Jeep", "Quick Reaction Vehicle", "Armoured APC")   # these drive between outposts

# Service every X km, and how many km each vehicle has done since its last service
INTERVAL = {"Patrol Jeep": 500, "Quick Reaction Vehicle": 500, "Armoured APC": 400,
            "Emergency Vehicle": 500, "Recon Drone": 300, "Helicopter": 300}
SEED_KM = {"JP-1": 410, "JP-2": 120, "QRT-1": 260, "APC-1": 250, "DR-1": 80, "HC-1": 150}

FAULTS = {
    "ground": ["Engine overheating", "Low tyre pressure", "Brake wear", "Battery voltage low"],
    "drone": ["Motor vibration high", "Battery cell imbalance", "GPS signal weak"],
    "helicopter": ["Rotor vibration high", "Hydraulic pressure low"],
}


def read_json(name):
    p = HERE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def is_air(t):
    return "Drone" in t or "Helicopter" in t


def kmh(t):
    return KMH.get(t, 60)


def burn_of(t):
    return BURN.get(t, 0.25)


def fault_list(t):
    if "Helicopter" in t:
        return FAULTS["helicopter"]
    if "Drone" in t:
        return FAULTS["drone"]
    return FAULTS["ground"]


def home_key(vid):
    return config.HOME.get(vid, "main")


def is_reserve(vid):
    return vid in config.HOME


def dist_m(a, b):
    dlat = (b[0] - a[0]) * 111000
    dlon = (b[1] - a[1]) * 111000 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlon)


def step_towards(pos, target, meters):
    d = dist_m(pos, target)
    if d <= meters:
        return target, meters - d, True
    f = meters / d
    return (pos[0] + (target[0] - pos[0]) * f,
            pos[1] + (target[1] - pos[1]) * f), 0, False


def follow(pos, s, meters):
    # Drive along the route in s["route"] for up to `meters`
    used = 0.0
    while meters > 0 and s["route"]:
        pos, left, arrived = step_towards(pos, s["route"][0], meters)
        used += meters - left
        meters = left
        if arrived:
            s["route"].pop(0)
        else:
            break
    return pos, used


def thin(pts, n=120):
    # Keep about n points so the map page stays light
    step = max(1, len(pts) // n)
    out = list(pts[::step])
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return [[round(p[0], 5), round(p[1], 5)] for p in out]


def notify(db, vid, level, text):
    # Create a notification, but only once while it is still open
    if db.query(Notice).filter(Notice.vehicle_id == vid, Notice.text == text,
                               Notice.status == "open").first():
        return
    db.add(Notice(vehicle_id=vid, level=level, text=text,
                  created=datetime.now().isoformat(timespec="seconds")))
    print(f"[{level}] {vid}: {text}")


def control(db, al, text):
    # A message to the control centre about one alert
    db.add(Notice(vehicle_id="CONTROL", level="ESCALATION", text=f"[#{al.id}] {text}",
                  created=datetime.now().isoformat(timespec="seconds")))
    print(f"[CONTROL CENTRE] alert #{al.id}: {text}")


def ensure_maint(db, vehicles):
    have = {m.vehicle_id for m in db.query(Maint).all()}
    for v in vehicles:
        if v.id in have:
            continue
        km = SEED_KM.get(v.id, 60 if is_reserve(v.id) else 100)
        wear = WEAR_BY_VEHICLE.get(v.id, DEFAULT_WEAR)
        db.add(Maint(vehicle_id=v.id, km_total=km + 1000, km_since_service=km,
                     interval_km=INTERVAL.get(v.type, 500),
                     health=round(100 - wear * km, 1),
                     last_service=str(date.today() - timedelta(days=int(km / 10)))))
    db.flush()


def needs_service(m):
    return bool(m.fault) or m.km_since_service >= m.interval_km


def run():
    # Clean up leftovers from an earlier run
    with SessionLocal() as db:
        db.query(Dispatch).filter(Dispatch.status.in_(["enroute", "onscene"])) \
            .update({"status": "done"}, synchronize_session=False)
        db.query(Alert).filter(Alert.status != "resolved") \
            .update({"status": "resolved"}, synchronize_session=False)
        db.commit()

    graph_file = HERE / "road_graph.graphml"
    if not graph_file.exists():
        print("road_graph.graphml not found. Run: python prepare_data.py")
        return

    G = ox.load_graphml(graph_file)
    Grev = G.reverse(copy=False)        # lets us measure the distance from every point TO one place
    coords = {n: (d["y"], d["x"]) for n, d in G.nodes(data=True)}
    node_list = list(coords)
    tree = cKDTree([[coords[n][0], coords[n][1] * 0.83] for n in node_list])

    def nearest(pos):
        _, i = tree.query([pos[0], pos[1] * 0.83])
        return node_list[int(i)]

    def make_route(a, b):
        try:
            return [coords[n] for n in nx.shortest_path(G, a, b, weight="length")]
        except Exception:
            return []

    # Home positions of every base (road bases are moved onto the nearest road)
    home_pos, home_node = {}, {}
    for key, b in config.BASES.items():
        p = (b["lat"], b["lon"])
        if b["kind"] == "ground":
            n = nearest(p)
            home_node[key] = n
            home_pos[key] = coords[n]
        else:
            home_pos[key] = p

    border_pts = [tuple(p) for line in read_json("border.json") for p in line]
    post_nodes = [nearest((p["lat"], p["lon"])) for p in read_json("posts.json")]

    def border_point():
        if not border_pts:
            return (random.uniform(S, N), random.uniform(W, E))
        lat, lon = random.choice(border_pts)
        return (lat + random.uniform(-0.05, 0.05), lon + random.uniform(-0.05, 0.05))

    states = {}
    counters = {"k": 0}
    dist_cache = {}       # alert id -> distances from every road point to that alert
    noted = set()         # control-centre messages already sent

    def get_state(v):
        s = states.get(v.id)
        if s is None:
            circuit = []
            if v.type in PATROLLERS and not is_reserve(v.id) and post_nodes:
                k = counters["k"]
                counters["k"] += 1
                if len(post_nodes) > 1:
                    circuit = [post_nodes[k % len(post_nodes)], post_nodes[(k + 1) % len(post_nodes)]]
                else:
                    circuit = [post_nodes[0]]
            s = {"route": [], "target": None, "mode": "patrol", "snapped": False,
                 "hold": 0, "wait": 0, "alert": None, "circuit": circuit, "ci": 0}
            states[v.id] = s
        return s

    # ---------- helpers for planning ----------
    def travel_km(v, dest, dist_to):
        pos = (v.lat, v.lon)
        if is_air(v.type):
            return dist_m(pos, dest) / 1000
        n = nearest(pos)
        d = dist_to.get(n)
        if d is None:
            return None
        return d / 1000 + dist_m(pos, coords[n]) / 1000

    def feasible(v, km, dest):
        # Enough fuel to reach the alert AND get back to its own base
        back = dist_m(dest, home_pos[home_key(v.id)]) / 1000 * (1 if is_air(v.type) else 1.4)
        return v.fuel >= burn_of(v.type) * (km + back) + 8

    def is_free(v, bad):
        return v.id not in bad and states[v.id]["mode"] in ("patrol", "returning") and v.fuel > 10

    def missing_roles(db, al):
        missing = list(TEAMS.get(al.severity, []))
        rows = db.query(Dispatch).filter(Dispatch.alert_id == al.id,
                                         Dispatch.status.in_(["enroute", "onscene", "done"])).all()
        for d in rows:
            if d.vehicle_type in missing:
                missing.remove(d.vehicle_type)
        return missing

    def start_mission(db, v, al, dest, km):
        s = states[v.id]
        if is_air(v.type):
            s["route"] = []
            shown = [(v.lat, v.lon), dest]
        else:
            route = make_route(nearest((v.lat, v.lon)), nearest(dest))
            if not route:
                route = [coords[nearest(dest)]]
            s["route"] = route
            shown = [(v.lat, v.lon)] + route
        s.update(mode="dispatch", target=dest, alert=al.id, wait=0)
        db.add(Dispatch(alert_id=al.id, vehicle_id=v.id, vehicle_type=v.type,
                        troops=TROOPS.get(v.type, 0), km=km,
                        eta_min=int(km / kmh(v.type) * 60),
                        route=json.dumps(thin(shown)), status="enroute"))
        db.flush()
        print(f"Alert #{al.id} ({al.severity}): sending {v.id}, {km:.0f} km")

    def find_diversion(db, role, al, dest, dist_to, vehicles, taken):
        # A vehicle that is still driving to a LESS urgent alert can be pulled away
        best = None
        for v in vehicles:
            if v.type != role or v.id in taken or states[v.id]["mode"] != "dispatch":
                continue
            old = db.query(Dispatch).filter(Dispatch.vehicle_id == v.id, Dispatch.status == "enroute") \
                .order_by(Dispatch.id.desc()).first()
            if old is None:
                continue
            old_al = db.get(Alert, old.alert_id)
            if old_al is None or RANK.get(old_al.severity, 0) >= RANK.get(al.severity, 0):
                continue
            km = travel_km(v, dest, dist_to)
            if km is None or not feasible(v, km, dest):
                continue
            eta = km / kmh(v.type) * 60
            if best is None or eta < best[2]:
                best = (v, km, eta, old, old_al)
        return best

    def plan_alert(db, al, vehicles, bad):
        missing = missing_roles(db, al)
        if not missing:
            return
        dest = (al.lat, al.lon)
        if al.id not in dist_cache:
            dist_cache[al.id] = nx.single_source_dijkstra_path_length(Grev, nearest(dest), weight="length")
        dist_to = dist_cache[al.id]
        limit = MAX_ETA.get(al.severity, 120)
        taken = set()

        for role in missing:
            local = best = None
            for v in vehicles:
                if v.type != role or v.id in taken or not is_free(v, bad):
                    continue
                km = travel_km(v, dest, dist_to)
                if km is None or not feasible(v, km, dest):
                    continue
                eta = km / kmh(v.type) * 60
                if best is None or eta < best[2]:
                    best = (v, km, eta)
                if not is_reserve(v.id) and (local is None or eta < local[2]):
                    local = (v, km, eta)

            choice = None
            if local and local[2] <= limit:
                choice = local                                  # a local unit is close enough
            else:
                key = (al.id, role, "slow")
                if key not in noted:
                    noted.add(key)
                    control(db, al, f"No {role} can reach {al.place} within {limit} min. "
                                    f"Control centre is requesting reinforcement.")
                if RANK.get(al.severity, 0) >= 3 and (best is None or best[2] > limit):
                    div = find_diversion(db, role, al, dest, dist_to, vehicles, taken)
                    if div and (best is None or div[2] < best[2]):
                        v, km, eta, old, old_al = div
                        old.status = "diverted"
                        control(db, old_al, f"{v.id} was diverted to higher-priority alert #{al.id}. "
                                            f"A replacement is needed.")
                        choice = (v, km, eta)
                if choice is None and best:
                    choice = best                               # fastest unit anywhere, even a reserve

            if choice is None:
                key = (al.id, role, "none")
                if key not in noted:
                    noted.add(key)
                    control(db, al, f"No {role} available anywhere. Waiting for a free unit.")
                continue

            v, km, eta = choice
            taken.add(v.id)
            start_mission(db, v, al, dest, km)
            base_name = config.BASES[home_key(v.id)]["name"]
            if is_reserve(v.id):
                control(db, al, f"Reinforcement: {v.id} ({role}) sent from {base_name}, ETA {int(eta)} min.")
            elif eta > limit:
                control(db, al, f"{v.id} ({role}) sent, ETA {int(eta)} min (target was {limit} min).")
            al.status = "dispatched"

    def manage_alerts(db, vehicles, maint):
        bad = {vid for vid, m in maint.items() if needs_service(m)}
        open_alerts = db.query(Alert).filter(Alert.status != "resolved").all()
        open_alerts.sort(key=lambda a: (-RANK.get(a.severity, 0), a.id))   # most urgent first

        for al in open_alerts:
            plan_alert(db, al, vehicles, bad)
            db.flush()
            ds = db.query(Dispatch).filter(Dispatch.alert_id == al.id).all()
            enroute = any(d.status == "enroute" for d in ds)
            onscene = any(d.status == "onscene" for d in ds)
            done = any(d.status == "done" for d in ds)
            if enroute:
                al.status = "dispatched"
            elif onscene:
                al.status = "onscene"
            elif done and not missing_roles(db, al):
                al.status = "resolved"
                dist_cache.pop(al.id, None)
                db.query(Notice).filter(Notice.vehicle_id == "CONTROL", Notice.text.like(f"[#{al.id}]%")) \
                    .update({"status": "done"}, synchronize_session=False)
            elif done:
                al.status = "dispatched"
            else:
                al.status = "new"

        # If the commander pressed Resolve, send the team home
        for al in db.query(Alert).filter(Alert.status == "resolved").order_by(Alert.id.desc()).limit(20).all():
            dist_cache.pop(al.id, None)
            for d in db.query(Dispatch).filter(Dispatch.alert_id == al.id,
                                               Dispatch.status.in_(["enroute", "onscene"])).all():
                s = states.get(d.vehicle_id)
                if s and s["alert"] == al.id:
                    s.update(mode="returning", route=[], target=None)
                d.status = "done"
        db.flush()

    # ---------- moving one vehicle ----------
    def move_vehicle(db, v, m, dt):
        s = states[v.id]
        air = is_air(v.type)
        key = home_key(v.id)
        hp = home_pos[key]
        hn = home_node.get(key)
        pos = (v.lat, v.lon)

        # First time: put road vehicles on a road, and send patrols out to their first outpost
        if not s["snapped"]:
            s["snapped"] = True
            if not air:
                pos = coords[nearest(pos)]
            if s["circuit"] and dist_m(pos, hp) < 3000:
                pos = coords[s["circuit"][0]]
            v.lat, v.lon = pos

        if v.fuel <= 0:
            v.status = "out of fuel"
            return

        meters = kmh(v.type) / 3.6 * SPEEDUP * dt
        burn = burn_of(v.type)
        used = 0.0
        at_home = dist_m(pos, hp) < 150

        # Waiting at the alert
        if s["mode"] == "onscene":
            s["hold"] -= dt
            v.status = f"on scene (alert #{s['alert']})"
            if s["hold"] <= 0:
                db.query(Dispatch).filter(Dispatch.vehicle_id == v.id, Dispatch.status == "onscene") \
                    .update({"status": "done"})
                s.update(mode="returning", route=[], target=None)
            return

        # Waiting at base for service or repair
        if s["mode"] == "maintenance":
            if needs_service(m):
                v.status = "under maintenance: " + (m.fault or "service overdue")
                return
            s["mode"] = "patrol"

        # Should a patrolling vehicle go home?
        if s["mode"] == "patrol":
            km_home = dist_m(pos, hp) / 1000 * (1 if air else 1.5)
            if needs_service(m):
                if at_home:
                    s["mode"] = "maintenance"
                    v.status = "under maintenance: " + (m.fault or "service overdue")
                    return
                s.update(mode="returning", route=[], target=None)
            elif v.fuel < burn * km_home + 8 and not at_home:
                s.update(mode="returning", route=[], target=None)

        mode = s["mode"]
        bf = 0.4 if mode == "patrol" else 1.0       # patrolling uses less fuel than racing to an alert
        home_reached = False

        if mode == "dispatch":
            if air:
                pos, left, arrived = step_towards(pos, s["target"], meters)
                used = meters - left
                reached = arrived
            else:
                pos, used = follow(pos, s, meters)
                reached = not s["route"]
            if reached:
                s["mode"] = "onscene"
                s["hold"] = ONSCENE_SECONDS
                db.query(Dispatch).filter(Dispatch.vehicle_id == v.id, Dispatch.status == "enroute") \
                    .update({"status": "onscene"})

        elif mode == "returning":
            if air:
                pos, left, arrived = step_towards(pos, hp, meters)
                used = meters - left
                home_reached = arrived
            else:
                if not s["route"] and hn is not None:
                    s["route"] = make_route(nearest(pos), hn)
                pos, used = follow(pos, s, meters)
                home_reached = not s["route"]

        else:  # patrol
            pm = meters * 0.6                       # patrols drive slower than responders
            if air and not is_reserve(v.id):
                if s["target"] is None:
                    s["target"] = border_point()
                pos, left, arrived = step_towards(pos, s["target"], pm)
                used = pm - left
                if arrived:
                    s["target"] = None
            elif s["circuit"]:
                if s["wait"] > 0:
                    s["wait"] -= dt
                else:
                    if not s["route"]:
                        s["ci"] = (s["ci"] + 1) % len(s["circuit"])
                        s["route"] = make_route(nearest(pos), s["circuit"][s["ci"]])
                    pos, used = follow(pos, s, pm)
                    if not s["route"]:
                        s["wait"] = random.uniform(6, 15)     # stop at the outpost for a while

        v.lat, v.lon = pos
        v.fuel = max(0.0, v.fuel - used / 1000 * burn * bf)

        # Wear and tear: each vehicle wears at its own speed
        km = used / 1000
        if km > 0:
            wear = WEAR_BY_VEHICLE.get(v.id, DEFAULT_WEAR)
            m.km_total += km
            m.km_since_service += km
            m.health = max(0, m.health - km * wear)
            if not m.fault and m.health < 70 and random.random() < 0.003:
                m.fault = random.choice(fault_list(v.type))
                notify(db, v.id, "FAULT", f"Fault: {m.fault}")

        # Reminders
        ratio = m.km_since_service / m.interval_km
        if ratio >= 1:
            notify(db, v.id, "WARNING", "Service overdue: vehicle will be held at base")
        elif ratio >= 0.85:
            notify(db, v.id, "INFO", "Service due soon")
        if m.health < 50:
            notify(db, v.id, "WARNING", "Health below 50%: failure risk")

        # Back at base: refuel, and stay if it needs service
        if home_reached:
            v.lat, v.lon = hp
            v.fuel = 100
            s.update(mode="maintenance" if needs_service(m) else "patrol", route=[], target=None, wait=0)

        mode = s["mode"]
        if mode == "dispatch":
            v.status = f"responding to alert #{s['alert']}"
        elif mode == "onscene":
            v.status = f"on scene (alert #{s['alert']})"
        elif mode == "maintenance":
            v.status = "under maintenance: " + (m.fault or "service overdue")
        elif mode == "returning":
            v.status = "returning to base"
        elif (air and not is_reserve(v.id)) or s["circuit"]:
            v.status = "holding at outpost" if s["wait"] > 0 else "patrol"
        else:
            v.status = "reserve standby" if is_reserve(v.id) else "standby at base"

    # ---------- main loop ----------
    print("Simulator ready. Vehicles are now moving.")
    last = time.time()
    last_plan = 0.0
    last_error = ""
    while True:
        now = time.time()
        dt = min(now - last, 3)
        last = now
        try:
            with SessionLocal() as db:
                vehicles = db.query(Vehicle).order_by(Vehicle.id).all()
                for v in vehicles:
                    get_state(v)
                ensure_maint(db, vehicles)
                maint = {m.vehicle_id: m for m in db.query(Maint).all()}
                if now - last_plan >= PLAN_EVERY:
                    last_plan = now
                    manage_alerts(db, vehicles, maint)
                for v in vehicles:
                    move_vehicle(db, v, maint[v.id], dt)
                db.commit()
        except Exception as err:
            if str(err) != last_error:           # print each new error once, but keep running
                last_error = str(err)
                print("Simulator error:")
                traceback.print_exc()
            time.sleep(1)
        time.sleep(TICK)