import os, hashlib, sqlite3
from datetime import datetime, timezone
import feedparser
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(title="Stoic Finance Agent", version="0.1.0")
DB = os.getenv("DB_PATH", "stoic.db")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None

SOURCES = {
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "BLS": "https://www.bls.gov/feed/bls_latest.rss",
    "SEC": "https://www.sec.gov/news/pressreleases.rss",
}

EDITORIAL = """You are the editor of an English-language financial education brand.
Mission: Understand markets. Ignore the noise.
Use news to teach. Write for an intelligent reader who may know little about finance.
Be calm, concise, factual and natural. Separate fact from interpretation.
Never invent causality, numbers, quotes, forecasts, or sources. Never give buy/sell advice.
Use $TICKER only when materially relevant. No clickbait, emoji spam, guru tone, or AI clichés.
Avoid: 'here's the thing', 'let's break it down', 'game changer', 'this changes everything'.
A draft should explain what happened, why it matters, and one reusable principle.
If there is no worthwhile educational angle, return exactly SILENCE."""

def db():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS drafts(
      id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, published TEXT,
      draft TEXT, status TEXT DEFAULT 'pending', created_at TEXT)""")
    c.commit()
    return c

def make_id(url, title):
    return hashlib.sha256((url+"|"+title).encode()).hexdigest()[:20]

def write_draft(item):
    if not client:
        return None
    prompt=f"""SOURCE: {item['source']}
TITLE: {item['title']}
SUMMARY: {item.get('summary','')}
PUBLISHED: {item.get('published','')}
SOURCE URL: {item['url']}

Write one X post or short thread. Do not add facts absent from the supplied source text.
If the source text is insufficient to write safely, output SILENCE."""
    r=client.responses.create(
        model=os.getenv("OPENAI_MODEL","gpt-5.6"),
        instructions=EDITORIAL,
        input=prompt,
    )
    return r.output_text.strip()

def collect():
    out=[]
    for source,url in SOURCES.items():
        feed=feedparser.parse(url)
        for e in feed.entries[:10]:
            link=e.get("link","")
            title=e.get("title","").strip()
            if not link or not title: continue
            out.append({
                "id":make_id(link,title), "source":source, "title":title,
                "url":link, "summary":e.get("summary",""),
                "published":e.get("published","")
            })
    return out

@app.get("/")
def root():
    return {"name":"Stoic Finance Agent","mode":"draft-only","status":"running"}

@app.get("/health")
def health():
    return {"ok":True}

@app.post("/run")
def run():
    c=db(); created=0; silent=0
    for item in collect():
        if c.execute("SELECT 1 FROM drafts WHERE id=?",(item["id"],)).fetchone():
            continue
        draft=write_draft(item)
        if draft == "SILENCE" or not draft:
            silent += 1
            continue
        c.execute("INSERT INTO drafts(id,source,title,url,published,draft,created_at) VALUES(?,?,?,?,?,?,?)",
          (item["id"],item["source"],item["title"],item["url"],item["published"],draft,datetime.now(timezone.utc).isoformat()))
        created += 1
    c.commit()
    return {"created":created,"silence":silent}

@app.get("/drafts")
def drafts(limit:int=20):
    c=db()
    rows=c.execute("SELECT id,source,title,url,published,draft,status,created_at FROM drafts ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
    keys=["id","source","title","url","published","draft","status","created_at"]
    return [dict(zip(keys,r)) for r in rows]

class Decision(BaseModel):
    status: str

@app.post("/drafts/{draft_id}/decision")
def decision(draft_id:str, body:Decision):
    if body.status not in {"approved","rejected","pending"}:
        return {"ok":False,"error":"status must be approved, rejected, or pending"}
    c=db(); c.execute("UPDATE drafts SET status=? WHERE id=?",(body.status,draft_id)); c.commit()
    return {"ok":True,"id":draft_id,"status":body.status}
