"""Offline stand-ins for the external services the workflow talks to.

- POST /v1/chat/completions   OpenAI-compatible endpoint backed by simple rules
                              (NOT a language model: it only lets you run the
                              workflow end to end without an API key).
- POST /hooks/slack           captures Slack incoming-webhook payloads
- POST /hooks/crm             captures CRM upserts
- POST /hooks/helpdesk        captures helpdesk tickets
- GET  /                      live page showing everything the workflow sent

Run:  uvicorn server:app --port 8787   (from this folder)
"""
from __future__ import annotations

import json
import re
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

app = FastAPI(title="Mock services for the n8n lead-triage demo")
EVENTS: deque[dict] = deque(maxlen=200)

FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "proton.me", "gmx.de"}

RULES = [
    ("spam", r"\b(crypto|backlinks?|seo services|you (have )?won|winner|guaranteed ranking|click here|bitcoin)\b"),
    ("billing", r"\b(invoice|refund|charged|charge|payment|billing|credit note|vat)\b"),
    ("support", r"\b(error|not working|broken|bug|issue|cannot|can't|unable|outage|down|tracking (is )?wrong|delayed|missing)\b"),
    ("partnership", r"\b(partner(ship)?|reseller|collaborat\w+|affiliate|white[- ]label)\b"),
    ("sales_lead", r"\b(quote|pricing|price|proposal|rfq|demo|interested in|looking for|need (a|an)? ?(carrier|partner|provider)|shipments?|pallets?|containers?|freight)\b"),
]
URGENT = r"\b(urgent|asap|immediately|today|right now|critical|outage|blocked|by tomorrow)\b"


def _classify(text: str, sender: str) -> dict:
    low = text.lower()
    scores = {cat: len(re.findall(rx, low)) for cat, rx in RULES}
    category = max(scores, key=scores.get) if any(scores.values()) else "other"
    if scores["spam"]:
        category = "spam"

    budget = re.search(r"(?:€|eur|\$|usd)\s?([\d][\d.,]*)\s?(k)?|([\d][\d.,]*)\s?(k)?\s?(?:€|eur|euros|usd|\$)", low)
    budget_value = None
    if budget:
        raw = (budget.group(1) or budget.group(3) or "").replace(",", "").replace(" ", "")
        try:
            budget_value = float(raw) * (1000 if (budget.group(2) or budget.group(4)) else 1)
        except ValueError:
            budget_value = None
    timeline = re.search(r"\b(next (week|month|quarter)|by (\w+ )?(january|february|march|april|may|june|july|august|september|october|november|december)|q[1-4]( 20\d\d)?|within \d+ (days|weeks)|asap)\b", low)
    domain = sender.split("@")[-1].lower() if "@" in sender else ""
    company = None
    if domain and domain not in FREE_MAIL:
        company = domain.split(".")[0].replace("-", " ").title()

    score = 0
    if category == "sales_lead":
        score = 35 + (25 if budget_value else 0) + (15 if timeline else 0) + (10 if company else 0)
        score += 10 if re.search(r"\b\d{2,}\s?(pallets?|containers?|shipments?|trucks?|tons?)\b", low) else 0
        score += 5 if re.search(URGENT, low) else 0
    urgency = "high" if re.search(URGENT, low) else ("medium" if category in ("support", "billing", "sales_lead") else "low")
    sentiment = "negative" if re.search(r"\b(unacceptable|disappointed|angry|frustrat\w+|terrible|again|still)\b", low) else (
        "positive" if re.search(r"\b(great|thanks|love|excited|happy)\b", low) else "neutral")
    body_text = text.split("Message:", 1)[1].strip() if "Message:" in text else text.strip()
    body_text = re.sub(r"^(hello|hi|hey|dear|bonjour)\b[^,.!\n]*[,.!]?\s*", "", body_text, flags=re.I)
    first = re.split(r"(?<=[.!?])\s+", body_text)[0]
    first = first[:1].upper() + first[1:]
    name = re.search(r"From:\s*([^<\n]+?)\s*<", text)
    product = re.search(r"\b(express|ltl|ftl|full truckload|cold chain|warehousing|customs|last[- ]mile)\b", low)
    return {
        "category": category,
        "urgency": urgency,
        "sentiment": sentiment,
        "language": "fr" if re.search(r"\b(bonjour|merci|nous|votre|devis)\b", low) else "en",
        "summary": first[:160],
        "company": company,
        "contact_name": name.group(1).strip() if name else None,
        "budget_eur": budget_value,
        "timeline": timeline.group(0) if timeline else None,
        "product_interest": product.group(0) if product else None,
        "lead_score": min(score, 100),
        "needs_human": category in ("other", "partnership"),
        "reasoning": f"rule-based mock: keyword hits {scores}",
    }


def _draft(prompt: str) -> str:
    name = re.search(r"From:\s*([^<\n]+)", prompt)
    first = (name.group(1).strip().split() or ["there"])[0] if name else "there"
    return (f"Hi {first},\n\nThank you for getting in touch. I've read your request and we'd be glad to help.\n\n"
            "Could we set up a 20-minute call this week to go through volumes, lanes and timing? "
            "I'll come prepared with a tailored quote.\n\nBest regards,\nThe Northwind sales team")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    system = " ".join(m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system")
    user = "\n".join(m.get("content", "") for m in body.get("messages", []) if m.get("role") == "user")
    if "JSON" in system:
        sender = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", user)
        content = json.dumps(_classify(user, sender.group(0) if sender else ""))
    else:
        content = _draft(user)
    EVENTS.appendleft({"at": time.time(), "service": "llm", "payload": {"task": "classify" if "JSON" in system else "draft"}})
    return {
        "id": f"mock-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "model": body.get("model", "mock-llm"),
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(content) // 4},
    }


@app.post("/hooks/{service}")
async def capture(service: str, request: Request):
    payload = await request.json()
    EVENTS.appendleft({"at": time.time(), "service": service, "payload": payload})
    if service == "crm":
        return {"ok": True, "contact_id": f"CRM-{len(EVENTS):04d}"}
    if service == "helpdesk":
        return {"ok": True, "ticket_id": f"TCK-{1000 + len(EVENTS)}"}
    if service == "slack":
        return PlainTextResponse("ok")  # Slack incoming webhooks answer with plain text
    return {"ok": True}


@app.get("/api/events")
def events(service: str | None = None):
    return [e for e in EVENTS if service in (None, e["service"])]


@app.delete("/api/events")
def clear():
    EVENTS.clear()
    return {"cleared": True}


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workflow outputs</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#eef0ea;--card:#fff;--ink:#1d2320;--muted:#66706a;--rule:#d5d9d0;--slack:#4a154b;--crm:#0b6e4f;--desk:#b5541b;--hot:#c7362f}
@media (prefers-color-scheme:dark){:root{--bg:#141815;--card:#1d221f;--ink:#e7ebe6;--muted:#97a29a;--rule:#2d342f;--slack:#d6a6d8;--crm:#6fd3ad;--desk:#f0a874;--hot:#ff8077}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Archivo,system-ui,sans-serif}
header{padding:24px clamp(16px,4vw,40px) 8px;display:flex;justify-content:space-between;align-items:baseline;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:22px;letter-spacing:-.01em}header p{margin:4px 0 0;color:var(--muted)}
button{font:inherit;border:1px solid var(--rule);background:var(--card);color:var(--ink);padding:6px 12px;border-radius:8px;cursor:pointer}
main{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;padding:16px clamp(16px,4vw,40px) 40px}
@media(max-width:980px){main{grid-template-columns:1fr}}
section h2{font-size:15px;margin:0 0 10px;display:flex;gap:8px;align-items:center}
section h2 i{width:10px;height:10px;border-radius:3px;display:inline-block}
.count{color:var(--muted);font-weight:400}
.card{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.card .top{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:12px;margin-bottom:4px}
.card b{font-weight:600}.card pre{white-space:pre-wrap;font:13px/1.45 Archivo,sans-serif;margin:8px 0 0;padding:10px;background:var(--bg);border-radius:6px}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;border:1px solid var(--rule);margin:4px 4px 0 0}
.tag.hot{border-color:var(--hot);color:var(--hot);font-weight:600}
.empty{color:var(--muted)}
</style></head><body>
<header><div><h1>What the workflow sent</h1><p>Messages captured from n8n: Slack alerts, CRM records and helpdesk tickets. Refreshes every 3 seconds.</p></div>
<button onclick="fetch('/api/events',{method:'DELETE'}).then(load)">Clear</button></header>
<main>
<section><h2><i style="background:var(--slack)"></i>Slack alerts <span class="count" id="n-slack"></span></h2><div id="slack"></div></section>
<section><h2><i style="background:var(--crm)"></i>CRM records <span class="count" id="n-crm"></span></h2><div id="crm"></div></section>
<section><h2><i style="background:var(--desk)"></i>Helpdesk tickets <span class="count" id="n-helpdesk"></span></h2><div id="helpdesk"></div></section>
</main>
<script>
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const t=ts=>new Date(ts*1000).toLocaleTimeString();
function slack(e){const p=e.payload;const blocks=(p.blocks||[]).filter(b=>b.type==='section').map(b=>b.text?b.text.text:(b.fields||[]).map(f=>f.text.replace('\\n',': ')).join('\\n')).join('\\n\\n');
 return `<div class="card"><div class="top"><span>#${esc(p.channel||'alerts')}</span><span>${t(e.at)}</span></div><b>${esc(p.text)}</b>${blocks?`<pre>${esc(blocks.replace(/[*_]/g,''))}</pre>`:''}</div>`}
function crm(e){const p=e.payload;return `<div class="card"><div class="top"><span>${esc(p.email)}</span><span>${t(e.at)}</span></div>
 <b>${esc(p.name)}</b>${p.company?` at ${esc(p.company)}`:''}<div>${esc(p.summary)}</div>
 <span class="tag ${p.lead_score>=70?'hot':''}">score ${p.lead_score}</span><span class="tag">${esc(p.lifecycle_stage)}</span>${p.budget_eur?`<span class="tag">budget €${Number(p.budget_eur).toLocaleString()}</span>`:''}${p.timeline?`<span class="tag">${esc(p.timeline)}</span>`:''}</div>`}
function desk(e){const p=e.payload;return `<div class="card"><div class="top"><span>${esc(p.queue)} queue</span><span>${t(e.at)}</span></div>
 <b>${esc(p.subject)}</b><div>${esc(p.description)}</div><span class="tag ${p.priority==='urgent'?'hot':''}">${esc(p.priority)}</span><span class="tag">${esc(p.requester_email)}</span></div>`}
async function load(){const ev=await (await fetch('/api/events')).json();
 for(const [k,f] of [['slack',slack],['crm',crm],['helpdesk',desk]]){const list=ev.filter(e=>e.service===k);
  document.getElementById(k).innerHTML=list.length?list.map(f).join(''):'<p class="empty">Nothing yet. Run scripts/send_samples.py.</p>';
  document.getElementById('n-'+k).textContent=list.length||''}}
load();setInterval(load,3000);
</script></body></html>"""
