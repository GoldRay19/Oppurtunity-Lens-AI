import os, json, sqlite3, uuid, re
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import httpx

DB_URL=os.getenv("DATABASE_URL","sqlite:///./opportunitylens.db")
DB_PATH=DB_URL.replace("sqlite:///","",1)
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)),exist_ok=True)
def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY,user_id TEXT,kind TEXT,payload TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    return c

def user_id(authorization: str|None):
    if authorization and authorization.startswith("Bearer "):
        token=authorization[7:]
        raw=os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if raw:
            try:
                import firebase_admin
                from firebase_admin import credentials,auth
                try: app=firebase_admin.get_app()
                except ValueError: app=firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
                return auth.verify_id_token(token,app=app)["uid"]
            except Exception: raise HTTPException(401,"Invalid Firebase token")
        if token!="demo-token": raise HTTPException(401,"Firebase authentication is not configured")
    return "demo-user"

class Payload(BaseModel):
    payload: dict=Field(default_factory=dict)
class Analyze(BaseModel):
    description: str
    title: str=""
class Match(BaseModel):
    opportunity: dict=Field(default_factory=dict)
class Copilot(BaseModel):
    task: str
    context: dict=Field(default_factory=dict)
    instructions: str=""
class Chat(BaseModel):
    message: str
    context: dict=Field(default_factory=dict)

app=FastAPI(title="OpportunityLens AI",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])

def get_workflow(name,payload):
    try: workflows=json.loads(os.getenv("ADAPTION_AI_WORKFLOWS_JSON","{}"))
    except Exception: workflows={}
    url=workflows.get(name)
    key=os.getenv("ADAPTION_AI_API_KEY")
    if not url or not key: return None
    # URL must be a verified workflow invocation URL from the user's Adaption AI workspace.
    with httpx.Client(timeout=45) as client:
        r=client.post(url,json=payload,headers={"Authorization":f"Bearer {key}"})
        r.raise_for_status()
        return r.json()

@app.get("/api/health")
def health(): return {"status":"ok","service":"OpportunityLens AI"}
@app.get("/api/config")
def config():
    try: w=json.loads(os.getenv("ADAPTION_AI_WORKFLOWS_JSON","{}"))
    except Exception: w={}
    return {"app_name":"OpportunityLens AI","ai_mode":"adaption_ai" if os.getenv("ADAPTION_AI_API_KEY") and w else "demo","firebase_enabled":bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))}

@app.get("/api/records/{kind}")
def records(kind:str,authorization:str|None=Header(default=None)):
    uid=user_id(authorization)
    with db() as c:
        rows=c.execute("SELECT * FROM records WHERE user_id=? AND kind=? ORDER BY created_at DESC",(uid,kind)).fetchall()
    return [{"id":r["id"],"kind":r["kind"],**json.loads(r["payload"]),"created_at":r["created_at"]} for r in rows]

@app.post("/api/records/{kind}")
def create(kind:str,b:Payload,authorization:str|None=Header(default=None)):
    uid=user_id(authorization); rid=str(uuid.uuid4())
    with db() as c:
        c.execute("INSERT INTO records(id,user_id,kind,payload) VALUES(?,?,?,?)",(rid,uid,kind,json.dumps(b.payload)))
    return {"id":rid,"kind":kind,**b.payload}

@app.patch("/api/records/{rid}")
def patch(rid:str,b:Payload,authorization:str|None=Header(default=None)):
    uid=user_id(authorization)
    with db() as c:
        row=c.execute("SELECT * FROM records WHERE id=? AND user_id=?",(rid,uid)).fetchone()
        if not row: raise HTTPException(404,"Record not found")
        p=json.loads(row["payload"]);p.update(b.payload)
        c.execute("UPDATE records SET payload=? WHERE id=?",(json.dumps(p),rid))
    return {"id":rid,"kind":row["kind"],**p}

@app.delete("/api/records/{rid}")
def delete(rid:str,authorization:str|None=Header(default=None)):
    with db() as c:
        cur=c.execute("DELETE FROM records WHERE id=? AND user_id=?",(rid,user_id(authorization)))
        if not cur.rowcount: raise HTTPException(404,"Record not found")
    return {"deleted":True}

@app.post("/api/ai/analyze-opportunity")
def analyze(b:Analyze,authorization:str|None=Header(default=None)):
    user_id(authorization)
    if not b.description.strip(): raise HTTPException(400,"Paste an opportunity description.")
    live=get_workflow("opportunity_analysis",b.model_dump())
    if live is not None: return {**live,"mode":"adaption_ai"}
    text=b.description; low=text.lower()
    skills=[s for s in ["python","javascript","react","fastapi","sql","java","machine learning","firebase","git","communication"] if s in low]
    deadline=re.search(r"(?:deadline|last date|apply by)\s*[:\-]?\s*([A-Za-z0-9 ,./-]{4,30})",text,re.I)
    return {"title":b.title or "Opportunity extracted from description","organization":"Not specified",
      "category":next((x for x in ["internship","scholarship","hackathon","fellowship","job","research"] if x in low),"other"),
      "summary":text[:650],"required_skills":skills,"qualifications":[],"eligibility":["Review original organizer requirements."],
      "deadline":deadline.group(1).strip() if deadline else None,"compensation":"Check original listing",
      "application_process":"See original organizer listing","mode":"demo_rules",
      "disclaimer":"Rule-based demo extraction, not live AI. Verify every detail with the organizer."}

@app.post("/api/ai/match")
def match(b:Match,authorization:str|None=Header(default=None)):
    uid=user_id(authorization)
    with db() as c:
        row=c.execute("SELECT payload FROM records WHERE user_id=? AND kind='profile' ORDER BY created_at DESC LIMIT 1",(uid,)).fetchone()
    profile=json.loads(row["payload"]) if row else {}
    skills={s.lower() for s in profile.get("skills",[])}
    req={s.lower() for s in b.opportunity.get("required_skills",[])}
    good=sorted(skills&req);missing=sorted(req-skills)
    score=round(70*len(good)/max(1,len(req))+(20 if profile.get("education") else 0)+(10 if profile.get("projects") else 0))
    return {"match_score":min(score,100),"matching_skills":good,"missing_skills":missing,"eligibility_status":"Manual verification required","reasons":[f"{len(good)} of {len(req)} listed skills match.","Education and restrictions require manual review."],"disclaimer":"Preliminary estimate only, not an official eligibility decision."}

@app.post("/api/ai/copilot")
def copilot(b:Copilot,authorization:str|None=Header(default=None)):
    user_id(authorization)
    live=get_workflow("application_copilot",b.model_dump())
    if live is not None:return {"content":live,"mode":"adaption_ai"}
    return {"mode":"demo_template","content":"Draft for review: I am interested in this opportunity because it aligns with my goals and experience. Through my work and projects, I have developed relevant foundations and would welcome the chance to contribute and learn.\n\n[Add a specific, truthful example from your experience.]","disclaimer":"Template only. Review and personalize; never submit invented claims."}

@app.post("/api/ai/chat")
def chat(b:Chat,authorization:str|None=Header(default=None)):
    user_id(authorization)
    live=get_workflow("career_assistant",b.model_dump())
    if live is not None:return {"reply":live,"mode":"adaption_ai"}
    return {"mode":"demo_template","reply":"Start by choosing one target role. Compare several real job descriptions, identify the most frequent skill gap, and turn it into a small practical project. Add your education, skills and goals to your profile for more relevant guidance. Connect an Adaption AI workflow for live AI responses."}

@app.get("/api/dashboard")
def dashboard(authorization:str|None=Header(default=None)):
    uid=user_id(authorization)
    def get(kind):
        with db() as c: rows=c.execute("SELECT payload FROM records WHERE user_id=? AND kind=?",(uid,kind)).fetchall()
        return [json.loads(r["payload"]) for r in rows]
    apps=get("application");opps=get("opportunity");books=get("bookmark")
    return {"opportunities_discovered":len(opps),"applications":len(apps),"bookmarks":len(books),
      "upcoming_deadlines":sum(bool(a.get("deadline")) for a in apps),"applications_by_status":{s:sum(a.get("status")==s for a in apps) for s in ["Interested","Preparing","Applied","Assessment","Interview","Selected","Rejected"]},
      "recent_opportunities":opps[:5],"recent_applications":apps[:5]}

dist=Path(__file__).resolve().parent.parent/"frontend"/"dist"
if dist.exists():
    if (dist/"assets").exists():app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")
    @app.get("/{path:path}")
    def frontend(path:str):
        f=dist/path
        return FileResponse(f if path and f.is_file() else dist/"index.html")
