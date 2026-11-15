from sqlalchemy import Column, Integer, String, Float
from database import Base


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(String, primary_key=True)
    type = Column(String)
    fuel = Column(Float)
    status = Column(String, default="patrol")
    lat = Column(Float)
    lon = Column(Float)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    camera = Column(String)
    place = Column(String)
    kind = Column(String)
    severity = Column(String)
    people = Column(Integer, default=0)
    vehicles = Column(Integer, default=0)
    lat = Column(Float)
    lon = Column(Float)
    created = Column(String)
    status = Column(String, default="new")


class Dispatch(Base):
    __tablename__ = "dispatches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(Integer)
    vehicle_id = Column(String)
    vehicle_type = Column(String)
    troops = Column(Integer, default=0)
    km = Column(Float)
    eta_min = Column(Integer)
    route = Column(String)
    status = Column(String, default="enroute")


class Maint(Base):
    __tablename__ = "maint"

    vehicle_id = Column(String, primary_key=True)
    km_total = Column(Float, default=0)
    km_since_service = Column(Float, default=0)
    interval_km = Column(Float, default=500)
    health = Column(Float, default=100)
    last_service = Column(String, default="")
    fault = Column(String, default="")


class Notice(Base):
    __tablename__ = "notices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(String)
    level = Column(String)       # INFO, WARNING or FAULT
    text = Column(String)
    created = Column(String)
    status = Column(String, default="open")