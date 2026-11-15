SECTOR_NAME = "Ladakh (Western sector)"
BASE_NAME = "Leh (sector base)"
BASE = (34.1526, 77.5771)               # latitude, longitude

# The area we use: west, south, east, north
AREA = (77.45, 33.45, 79.10, 34.25)

MAP_CENTER = (33.85, 78.3)
MAP_ZOOM = 8

# Bases. Everything except Leh is SIMULATED and its position is only approximate.
# kind "ground" = road vehicles can start here, "air" = helicopters only.
BASES = {
    "main":    {"name": BASE_NAME, "lat": BASE[0], "lon": BASE[1], "kind": "ground"},
    "reserve": {"name": "Forward Reserve Base (simulated)", "lat": 33.95, "lon": 78.05, "kind": "ground"},
    "kargil":  {"name": "Kargil Air Detachment (simulated)", "lat": 34.55, "lon": 76.13, "kind": "air"},
    "rear":    {"name": "Rear Air Base (simulated)", "lat": 30.67, "lon": 76.79, "kind": "air"},
}

# Reserve units and their home base. Any vehicle not listed here belongs to Leh.
HOME = {
    "R-QRV-1": "reserve",
    "R-APC-1": "reserve",
    "R-EV-1": "reserve",
    "R-HC-1": "kargil",
    "R-HC-2": "rear",
}