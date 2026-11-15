import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import config
import predictor
from database import SessionLocal
from maintenance import status_of
from models import Alert, Dispatch, Maint, Notice, Vehicle

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- 1. Collect the facts from the database ----------
def collect_facts(db):
    alerts = db.query(Alert).order_by(Alert.id).all()
    totals = {"alerts": len(alerts), "LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0,
              "resolved": 0, "open": 0, "troops": 0}
    incidents = []
    for a in alerts:
        totals[a.severity] = totals.get(a.severity, 0) + 1
        totals["resolved" if a.status == "resolved" else "open"] += 1
        ds = db.query(Dispatch).filter(Dispatch.alert_id == a.id).all()
        troops = sum(d.troops for d in ds)
        totals["troops"] += troops
        incidents.append({
            "id": a.id, "time": a.created.replace("T", " "), "place": a.place,
            "camera": a.camera, "severity": a.severity, "people": a.people,
            "vehicles": a.vehicles, "status": a.status, "troops": troops,
            "team": [{"vehicle": d.vehicle_id, "type": d.vehicle_type,
                      "km": round(d.km, 1), "eta_min": d.eta_min} for d in ds],
        })

    vehicles = {v.id: v for v in db.query(Vehicle).all()}
    fleet = []
    for m in db.query(Maint).order_by(Maint.vehicle_id).all():
        v = vehicles.get(m.vehicle_id)
        res = predictor.predict(m.vehicle_id, m.health)
        km_left = None
        risk = "UNKNOWN"
        if res is not None:
            km_left = 0 if m.fault else round(res[0])
            risk = "HIGH" if km_left < 150 else "MEDIUM" if km_left < 300 else "LOW"
        fleet.append({
            "vehicle": m.vehicle_id, "type": v.type if v else "",
            "fuel": round(v.fuel) if v else None, "health": round(m.health, 1),
            "service_status": status_of(m), "km_since_service": round(m.km_since_service),
            "interval_km": round(m.interval_km), "fault": m.fault,
            "predicted_km_left": km_left, "failure_risk": risk,
        })

    notices = [{"vehicle": n.vehicle_id, "level": n.level, "text": n.text}
               for n in db.query(Notice).filter(Notice.status == "open").all()]

    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "sector": config.SECTOR_NAME, "base": config.BASE_NAME,
            "totals": totals, "incidents": incidents, "fleet": fleet,
            "open_notices": notices}


# ---------- 2. Template report (always works) ----------
def template_report(f):
    t = f["totals"]
    L = ["SHIFT REPORT",
         f"Sector: {f['sector']}   Base: {f['base']}",
         f"Generated: {f['generated']}", "", "1. SUMMARY"]
    if t["alerts"] == 0:
        L.append("No intrusion alerts were raised in this period.")
    else:
        L.append(f"{t['alerts']} alert(s) were raised: {t['LOW']} low, {t['MEDIUM']} medium, "
                 f"{t['HIGH']} high and {t['CRITICAL']} critical. {t['resolved']} resolved, "
                 f"{t['open']} still open. {t['troops']} troops were committed in total.")

    L += ["", "2. INCIDENTS"]
    if not f["incidents"]:
        L.append("  None.")
    for i in f["incidents"]:
        team = ", ".join(f"{x['vehicle']} ({x['km']} km)" for x in i["team"]) or "no vehicle sent"
        L.append(f"  #{i['id']}  {i['time']}  {i['severity']} at {i['place']} "
                 f"({i['people']} people, {i['vehicles']} vehicles). Team: {team}. "
                 f"Status: {i['status']}.")

    L += ["", "3. FLEET AND MAINTENANCE"]
    for v in f["fleet"]:
        line = (f"  {v['vehicle']} ({v['type']}): fuel {v['fuel']}%, health {v['health']}%, "
                f"service {v['service_status']}")
        if v["fault"]:
            line += f", FAULT: {v['fault']}"
        L.append(line)

    L += ["", "4. PREDICTIVE MAINTENANCE"]
    for v in f["fleet"]:
        if v["predicted_km_left"] is not None:
            L.append(f"  {v['vehicle']}: failure risk {v['failure_risk']}, "
                     f"about {v['predicted_km_left']} km left before maintenance is needed")

    L += ["", "5. RECOMMENDED ACTIONS"]
    acts = []
    for v in f["fleet"]:
        if v["fault"]:
            acts.append(f"Repair {v['vehicle']} ({v['fault']}).")
        elif v["service_status"] == "OVERDUE":
            acts.append(f"Service {v['vehicle']}: service is overdue.")
        elif v["service_status"] == "DUE SOON":
            acts.append(f"Plan service for {v['vehicle']}: due soon.")
        if v["failure_risk"] == "HIGH" and not v["fault"]:
            acts.append(f"Schedule maintenance for {v['vehicle']}: high predicted failure risk.")
        if v["fuel"] is not None and v["fuel"] < 25:
            acts.append(f"Refuel {v['vehicle']} (fuel {v['fuel']}%).")
    if t["open"]:
        acts.append(f"Close or review the {t['open']} open alert(s).")
    if t["HIGH"] + t["CRITICAL"]:
        acts.append("Review the high and critical incidents with the sector commander.")
    L += [f"  - {a}" for a in dict.fromkeys(acts)] or ["  - No action required."]
    return "\n".join(L)


# ---------- 3. AI report (optional) ----------
def ai_report(f):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.environ.get("REPORT_MODEL", "claude-sonnet-5-5"),
            max_tokens=1500,
            system=("You write shift reports for a border-patrol control room. "
                    "Use ONLY the facts in the JSON the user sends. Never invent numbers, "
                    "names, places or events. Write plain text, no markdown symbols. "
                    "Use these headings in capitals: SUMMARY, INCIDENTS, FLEET AND "
                    "MAINTENANCE, PREDICTIVE MAINTENANCE, RECOMMENDED ACTIONS. "
                    "Keep it clear and short."),
            messages=[{"role": "user", "content": json.dumps(f)}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception as err:
        print("AI report failed:", type(err).__name__, err)
        return None


@router.get("/report")
def report(mode: str = "template", db: Session = Depends(get_db)):
    facts = collect_facts(db)
    if mode == "ai":
        text = ai_report(facts)
        if text:
            return {"mode_used": "ai", "text": text, "facts": facts}
        return {"mode_used": "template (AI not available)",
                "text": template_report(facts), "facts": facts}
    return {"mode_used": "template", "text": template_report(facts), "facts": facts}