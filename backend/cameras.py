import time

from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter()
FRAMES = {}      # camera name -> (jpeg picture, time received)


@router.post("/camera/{cam}/frame")
async def put_frame(cam: str, request: Request):
    FRAMES[cam] = (await request.body(), time.time())
    return {"ok": True}


@router.get("/camera/{cam}/frame.jpg")
def get_frame(cam: str):
    item = FRAMES.get(cam)
    if item is None or time.time() - item[1] > 5:
        raise HTTPException(status_code=404, detail="No signal")
    return Response(content=item[0], media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/cameras")
def list_cameras():
    now = time.time()
    return [{"id": c, "live": now - t < 5} for c, (b, t) in sorted(FRAMES.items())]