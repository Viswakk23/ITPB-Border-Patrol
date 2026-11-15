import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from alerts import ACKS, get_db
from models import Alert, Dispatch, Maint, Vehicle

router = APIRouter()


@router.get("/console")
def list_vehicles(db: Session = Depends(get_db)):
    return [{"id": v.id, "type": v.type} for v in db.query(Vehicle).order_by(Vehicle.id)]


@router.get("/console/{vid}")
def console(vid: str, db: Session = Depends(get_db)):
    v = db.get(Vehicle, vid)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    m = db.get(Maint, vid)

    order = None
    d = db.query(Dispatch).filter(Dispatch.vehicle_id == vid,
                                  Dispatch.status.in_(["enroute", "onscene"])) \
        .order_by(Dispatch.id.desc()).first()
    if d:
        a = db.get(Alert, d.alert_id)
        if a and a.status != "resolved":
            team = db.query(Dispatch).filter(Dispatch.alert_id == a.id).all()
            order = {
                "alert_id": a.id, "severity": a.severity, "place": a.place,
                "kind": a.kind, "people": a.people, "vehicles": a.vehicles,
                "lat": a.lat, "lon": a.lon, "created": a.created,
                "km": round(d.km, 1), "eta_min": d.eta_min, "status": d.status,
                "route": json.loads(d.route) if d.route else [],
                "acked": (a.id, vid) in ACKS,
                "team": [{"vehicle": t.vehicle_id, "type": t.vehicle_type, "troops": t.troops}
                         for t in team],
            }

    return {"id": v.id, "type": v.type, "fuel": round(v.fuel), "status": v.status,
            "lat": v.lat, "lon": v.lon,
            "health": round(m.health) if m else None,
            "service": m.fault or ("service overdue" if m and m.km_since_service >= m.interval_km else "") if m else "",
            "order": order}