import random

# How fast each vehicle wears out (health points lost per km).
# In real life this is hidden. The AI must work it out from the sensors.
WEAR_BY_VEHICLE = {"JP-1": 0.08, "JP-2": 0.12, "QRT-1": 0.10,
                   "APC-1": 0.15, "DR-1": 0.06, "HC-1": 0.09}


def read_sensors(health, wear, rng=random):
    # Simulated sensors: they get worse with damage and with faster wear
    damage = 100 - health
    temp = 70 + damage * 0.5 + wear * 80 + rng.gauss(0, 2)      # engine temperature, C
    vib = 1.0 + damage * 0.03 + wear * 10 + rng.gauss(0, 0.15)  # vibration, mm/s
    volt = 13.8 - damage * 0.02 + rng.gauss(0, 0.1)             # battery voltage, V
    return temp, vib, volt