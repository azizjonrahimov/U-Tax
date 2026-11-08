"""Stand-in for the U-Tax gateway's market routes (test only)."""
import hashlib, os
from fastapi import FastAPI, Request, UploadFile, HTTPException
app = FastAPI(); JOBS = {}; TOKEN = os.environ["MARKET_TOKEN"]
def auth(r: Request):
    if r.headers.get("authorization") != f"Bearer {TOKEN}": raise HTTPException(401)
@app.post("/test/post")
async def post(job: dict):
    JOBS[job["job_id"]] = {**job, "status": "open"}; return JOBS[job["job_id"]]
@app.get("/market/jobs")
async def jobs(request: Request, status: str = "open"):
    auth(request); return {"jobs": [j for j in JOBS.values() if j["status"] == status]}
@app.post("/market/jobs/{jid}/claim")
async def claim(jid: str, request: Request):
    auth(request); body = await request.json(); j = JOBS[jid]
    if j["status"] != "open": raise HTTPException(409)
    j.update(status="claimed", wallet=body["wallet"], invoice_url=body["invoice_url"]); return j
@app.post("/market/jobs/{jid}/submit")
async def submit(jid: str, request: Request, file: UploadFile):
    auth(request); data = await file.read(); JOBS[jid].update(status="submitted", artifact=data.hex(), sha256=hashlib.sha256(data).hexdigest()); return {"ok": True}
@app.get("/test/job/{jid}")
async def get(jid: str): return JOBS[jid]
