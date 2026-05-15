"""
CDR Analyzer  —  Full Edition
==============================
All previous features +
  CASE-NAME QUERIES  (new in this version):
  ┌─────────────────────────────────────────────────────────────────┐
  │  "Show CDR linked to case342"                                   │
  │  "all records of case11"                                        │
  │  "find common b party numbers in case11 and case234"            │
  │  "find all imeis used in case11"                                │
  │  "find all imsis used in case11"                                │
  │  "first and last call of case11"                                │
  │  "first call of case11"   /  "last call of case11"             │
  │  "top contacts of case11"                                       │
  │  "frequent callers of case11"                                   │
  │  "frequent contacted of case11"                                 │
  │  "weekly summary of case11"                                     │
  │  "daily distribution of case11"                                 │
  │  "monthly summary of case11"                                    │
  │  "all imei of case11 and case22"  (multi-case)                  │
  │  "common b party in case11 and case22"                          │
  └─────────────────────────────────────────────────────────────────┘

  Join path (case name → CDR records):
    CrimeRegistry.Crime  →  CrimeRegistry._id
    CrimeRegistry._id    →  DataNexus.CrimeID
    DataNexus._id        →  CallDetailRecords.seq_id  (array field)

  All existing queries unchanged.
"""

import pymongo, json, re, sys, os, csv, time, traceback, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from ollama import Client as OllamaClient
from dateutil import parser as dateutil_parser

# ─────────────────────────────────────────────────────────────────
#  GLOBAL LIMITS
# ─────────────────────────────────────────────────────────────────
LLM_TIMEOUT_QUERY   = 20
LLM_TIMEOUT_SUMMARY = 30
MAX_RECORDS         = 1000
MAX_AGG_DOCS        = 5000
MAX_RESULT_MB       = 50
CURSOR_BATCH        = 200
CASE_FILE_PATH      = os.environ.get("CDR_CASE_FILE", "cases.json")

# ─────────────────────────────────────────────────────────────────
#  INDEX MAP
# ─────────────────────────────────────────────────────────────────
CDR_INDEXES = {
    "A_Party":"A_Party_1","B_Party":"B_Party_1","SDateTime":"SDateTime_1",
    "First_CGI":"First_CGI_1","IMEI":"IMEI_1","IMSI":"IMSI_1","seq_id":"seq_id_1",
}
CDR_INDEX_PRIORITY = ["A_Party","B_Party","IMEI","IMSI","First_CGI","SDateTime","seq_id"]
CDR_NON_INDEXED    = {"Call_Type","Duration","TowerID","Location","EDateTime"}

# ─────────────────────────────────────────────────────────────────
#  CONNECTION POOL
# ─────────────────────────────────────────────────────────────────
_mongo_client = None

def get_client(uri="mongodb://localhost:27017/"):
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = pymongo.MongoClient(
            uri, maxPoolSize=10, serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000, socketTimeoutMS=30000,
        )
    return _mongo_client

def get_collection(db, col, uri="mongodb://localhost:27017/"):
    return get_client(uri)[db][col]

# ─────────────────────────────────────────────────────────────────
#  OLLAMA / LLM
# ─────────────────────────────────────────────────────────────────
_ollama = OllamaClient()
MODEL   = "qwen2.5:7b"

def _llm_safe(messages, timeout=LLM_TIMEOUT_QUERY):
    box = [None, None]
    def _run():
        try:
            box[0] = _ollama.chat(model=MODEL, messages=messages, stream=False,
                                  options={"num_predict": 512})["message"]["content"].strip()
        except Exception as e:
            box[1] = str(e)
    t = threading.Thread(target=_run, daemon=True)
    t.start(); t.join(timeout=timeout)
    if t.is_alive(): print(f"[LLM] ⚠ TIMEOUT {timeout}s"); return None
    if box[1]:       print(f"[LLM] error: {box[1]}");       return None
    return box[0]

def _llm_stream_summary(messages, timeout=LLM_TIMEOUT_SUMMARY):
    buf, done = [], threading.Event()
    def _run():
        try:
            for c in _ollama.chat(model=MODEL, messages=messages, stream=True):
                buf.append(c["message"]["content"])
                if done.is_set(): break
        except Exception as e:
            buf.append(f"\n[err:{e}]")
    t = threading.Thread(target=_run, daemon=True)
    t.start(); t.join(timeout=timeout)
    if t.is_alive(): done.set()
    return "".join(buf).strip() or "Summary unavailable (LLM timeout)."

# ─────────────────────────────────────────────────────────────────
#  PROMPTS
# ─────────────────────────────────────────────────────────────────
CDR_SYSTEM_PROMPT = """
You are a MongoDB query generator for CDR (Call Detail Records).

EXACT SCHEMA:
  A_Party   string  TARGET number
  B_Party   string  other party
  SDateTime ISODate call/SMS start
  EDateTime ISODate call/SMS end
  SDate     ISODate date only
  STime     string  HH:MM:SS
  Duration  number  seconds
  Call_Type string  EXACT: "CALL_OUT"|"CALL_IN"|"SMS_OUT"|"SMS_IN"
  First_CGI string  cell tower CGI
  IMEI      string  device IMEI
  IMSI      string  subscriber identity
  seq_id    array   sequence IDs

INDEXED: A_Party,B_Party,SDateTime,First_CGI,IMEI,IMSI,seq_id
NON-INDEXED: Call_Type,Duration,EDateTime

RULES:
1. "target"=A_Party. Never put target in B_Party unless user says "b party"/"called by".
2. Call_Type EXACT: CALL_OUT,CALL_IN,SMS_IN,SMS_OUT
3. outgoing=CALL_OUT incoming=CALL_IN calls=$in:[CALL_IN,CALL_OUT] SMS=$in:[SMS_IN,SMS_OUT]
4. Return ONLY raw JSON.

EXAMPLES:
User: calls of target 7993677482
{"A_Party":"7993677482","Call_Type":{"$in":["CALL_IN","CALL_OUT"]}}
User: outgoing calls of 7993677482 on 5th feb 2024 10am to 2pm
{"A_Party":"7993677482","Call_Type":"CALL_OUT","SDateTime":{"$gte":"2024-02-05T10:00:00Z","$lte":"2024-02-05T14:00:00Z"}}
User: SMS by 7993677482
{"A_Party":"7993677482","Call_Type":{"$in":["SMS_IN","SMS_OUT"]}}
User: records where 9047363813 is b party
{"B_Party":"9047363813"}
User: night time calls of 9942131915
{"A_Party":"9942131915","$expr":{"$or":[{"$gte":[{"$hour":"$SDateTime"},18]},{"$lte":[{"$hour":"$SDateTime"},5]}]}}
User: give me target numbers starting with 79
{"A_Party":{"$regex":"^79"}}
"""

SUMMARY_PROMPT = """You are a telecom intelligence analyst.
Summarize CDR analytics in bullet points. Max 200 words.
Focus on: key patterns, peak hours, top contacts, anomalies."""

DATE_KEYS = {"SDateTime","EDateTime","$gte","$lte"}

# ─────────────────────────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────────────────────────
def _parse_dt(s):
    dt = dateutil_parser.parse(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def force_dates(data):
    if isinstance(data, dict):
        return {k: (_parse_dt(v) if k in DATE_KEYS and isinstance(v, str) else force_dates(v))
                for k, v in data.items()}
    if isinstance(data, list): return [force_dates(i) for i in data]
    return data

def serialize_doc(doc):
    out = {}
    for k, v in doc.items():
        if k == "_id":                out[k] = str(v)
        elif isinstance(v, datetime): out[k] = v.isoformat()
        elif isinstance(v, dict):     out[k] = serialize_doc(v)
        else:                         out[k] = v
    return out

def secs_human(s):
    s = int(s or 0)
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s//60}m {s%60}s"
    return f"{s//3600}h {(s%3600)//60}m"

def _utc(v):
    if v is None: return None
    return v if (isinstance(v, datetime) and v.tzinfo) else \
           (v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) else None)

def _fmt(v):
    v = _utc(v)
    return v.strftime("%d/%b/%Y %H:%M:%S") if v else "-"

def _day_range(d):
    return (datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc),
            datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc))

# ─────────────────────────────────────────────────────────────────
#  DB HELPERS
# ─────────────────────────────────────────────────────────────────
def _fetch_cursor(cursor, limit=MAX_RECORDS):
    out = []
    for doc in cursor.batch_size(CURSOR_BATCH):
        out.append(doc)
        if len(out) >= limit: break
    return out

def _agg_safe(col, pipeline, hint=None, limit=MAX_RECORDS):
    opts = {"hint": hint} if hint else {}
    return _fetch_cursor(col.aggregate(pipeline, **opts), limit=limit)

def _estimate_doc_count(col, query):
    try: return col.database.command({"count": col.name, "query": query}).get("n", 0)
    except: return 0

def _memory_guard(col, query, hint, limit, avg_bytes=2048):
    est_mb = (min(_estimate_doc_count(col, query), limit) * avg_bytes) / (1024 * 1024)
    if est_mb > MAX_RESULT_MB:
        raise MemoryError(f"Estimated {est_mb:.0f} MB > {MAX_RESULT_MB} MB. Narrow your filter.")

def pick_cdr_hint(q):
    for f in CDR_INDEX_PRIORITY:
        if f in q: return CDR_INDEXES[f]
    return None

def split_query(q, non_idx):
    idx, flt = {}, {}
    for k, v in q.items(): (flt if k in non_idx else idx)[k] = v
    return idx, flt

def _proj():
    return {"_id":1,"A_Party":1,"B_Party":1,"SDateTime":1,"EDateTime":1,
            "SDate":1,"STime":1,"Duration":1,"Call_Type":1,"First_CGI":1,
            "IMEI":1,"IMSI":1,"seq_id":1}

# ─────────────────────────────────────────────────────────────────
#  CASE REGISTRY HELPERS
#  Forward join:  case_name → CrimeRegistry._id → DataNexus.CrimeID
#                 → CallDetailRecords.seq_id
#  Reverse join:  seq_id → DataNexus._id → CrimeID → CrimeRegistry.Crime
# ─────────────────────────────────────────────────────────────────

def _resolve_case_names_to_seq_ids(case_names: list, mongo_uri: str) -> dict:
    """
    Forward join: given a list of case name strings,
    return {case_name: [seq_id, ...]} mapping.

    Path: CrimeRegistry.Crime == case_name
          → CrimeRegistry._id
          → DataNexus.CrimeID
          → DataNexus._id  (which is the seq_id)
    """
    if not case_names:
        return {}
    db = get_client(mongo_uri)["CDR"]

    # Step 1: CrimeRegistry  name → _id
    cr_rows = list(db["CrimeRegistry"].find(
        {"Crime": {"$in": case_names}},
        {"_id": 1, "Crime": 1}
    ))
    # {crime_id: case_name}
    cid_to_name = {str(r["_id"]): r.get("Crime", "") for r in cr_rows}
    if not cid_to_name:
        return {}

    # Step 2: DataNexus  CrimeID → _id (seq_id)
    dn_rows = list(db["DataNexus"].find(
        {"CrimeID": {"$in": list(cid_to_name.keys())}},
        {"_id": 1, "CrimeID": 1}
    ))

    case_to_seqids: dict = {}
    for row in dn_rows:
        cid = str(row.get("CrimeID", ""))
        cname = cid_to_name.get(cid, "")
        if not cname:
            continue
        case_to_seqids.setdefault(cname, []).append(row["_id"])
    return case_to_seqids


def _get_targets_for_cases(case_names: list, mongo_uri: str) -> dict:
    """
    Return {case_name: [A_Party numbers]} by:
      case_name → seq_ids → CDR A_Party distinct values
    """
    case_to_seqids = _resolve_case_names_to_seq_ids(case_names, mongo_uri)
    db = get_client(mongo_uri)["CDR"]
    col = db["CallDetailRecords"]
    result = {}
    for cname, seq_ids in case_to_seqids.items():
        rows = _agg_safe(col, [
            {"$match":  {"seq_id": {"$in": seq_ids}}},
            {"$group":  {"_id": "$A_Party"}},
        ], hint="seq_id_1", limit=10000)
        result[cname] = [r["_id"] for r in rows if r.get("_id")]
    return result


def _seq_ids_for_cases(case_names: list, mongo_uri: str) -> list:
    """Flat list of all seq_ids across given case names."""
    mapping = _resolve_case_names_to_seq_ids(case_names, mongo_uri)
    return list({s for seqs in mapping.values() for s in seqs})


# ─────────────────────────────────────────────────────────────────
#  CASE ENRICHMENT  (reverse join: seq_id → case name, 2 round-trips)
# ─────────────────────────────────────────────────────────────────
def enrich_records_with_case(records, mongo_uri="mongodb://localhost:27017/"):
    all_seq = []
    for rec in records:
        sid = rec.get("seq_id")
        if sid is None: continue
        all_seq.extend(sid if isinstance(sid, list) else [sid])

    if not all_seq:
        for rec in records:
            rec["crime_ids"] = ""; rec["case_names"] = ""; rec.pop("seq_id", None)
        return records

    db = get_client(mongo_uri)["CDR"]

    # Round-trip 1: seq_id → CrimeID
    nexus = _agg_safe(db["DataNexus"],
        [{"$match": {"_id": {"$in": all_seq}}}, {"$project": {"_id": 1, "CrimeID": 1}}],
        limit=len(all_seq) * 10)
    seq_to_crimes = {}
    for row in nexus:
        sid = str(row["_id"]); cid = row.get("CrimeID")
        if cid is None: continue
        crimes = cid if isinstance(cid, list) else [cid]
        seq_to_crimes.setdefault(sid, []).extend(str(c) for c in crimes)

    all_cids = list({c for cs in seq_to_crimes.values() for c in cs})

    # Round-trip 2: CrimeID → Crime name
    crime_rows = _agg_safe(db["CrimeRegistry"],
        [{"$match": {"_id": {"$in": all_cids}}}, {"$project": {"_id": 1, "Crime": 1}}],
        limit=len(all_cids) * 2)
    cid_to_name = {str(r["_id"]): r.get("Crime", str(r["_id"])) for r in crime_rows}

    for rec in records:
        sid = rec.pop("seq_id", None)
        if sid is None:
            rec["crime_ids"] = ""; rec["case_names"] = ""; continue
        seq_list = sid if isinstance(sid, list) else [sid]
        cids = list(dict.fromkeys(c for s in seq_list for c in seq_to_crimes.get(str(s), [])))
        rec["crime_ids"]  = " , ".join(cids)
        rec["case_names"] = " , ".join(cid_to_name.get(c, c) for c in cids)
    return records


def _enrich_and_summarise(result, mongo_uri):
    records = result.get("records", [])
    if records:
        t0 = time.time()
        enrich_records_with_case(records, mongo_uri)
        print(f"[Enrich] {round(time.time()-t0,3)}s qtype={result.get('query_type','?')}")
    all_cases = {}
    for rec in records:
        for name in (rec.get("case_names") or "").split(" , "):
            if name: all_cases[name] = all_cases.get(name, 0) + 1
    result["case_summary"] = {"unique_cases": len(all_cases), "case_hit_count": all_cases}
    return result


# ─────────────────────────────────────────────────────────────────
#  CASE DATABASE (file-based, kept for compatibility)
# ─────────────────────────────────────────────────────────────────
class CaseDatabase:
    def __init__(self):
        self.cases = []; self.number_index = defaultdict(list); self._loaded = False

    def load(self, path=CASE_FILE_PATH):
        if not os.path.exists(path): print(f"[CaseDB] not found: {path}"); return self
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext in (".json", ".jsonl"): self._load_json(path)
            elif ext == ".csv":            self._load_csv(path)
            else:                          self._load_txt(path)
            self._build_index(); self._loaded = True
            print(f"[CaseDB] {len(self.cases)} cases, {len(self.number_index)} numbers")
        except Exception as e:
            print(f"[CaseDB] ⚠ {e}")
        return self

    def _load_json(self, path):
        with open(path, encoding="utf-8") as f: raw = f.read().strip()
        recs = json.loads(raw) if raw.startswith("[") else \
               [json.loads(l) for l in raw.splitlines() if l.strip()]
        for r in recs: self.cases.append(self._norm(r))

    def _load_csv(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                def sn(s): return [x.strip() for x in re.split(r"[;,\s]+", s)
                                    if re.match(r"^\d{7,15}$", x.strip())]
                t = sn(row.get("targets", "") or ""); p = sn(row.get("participants", "") or "")
                self.cases.append({"case_id": row.get("case_id", f"CASE{i+1:04d}"),
                    "name": row.get("case_name", row.get("name", f"CASE{i+1:04d}")),
                    "targets": t, "participants": p, "all_numbers": list(set(t+p)), "raw": dict(row)})

    def _load_txt(self, path):
        with open(path, encoding="utf-8") as f: content = f.read()
        num_re = re.compile(r"\b(\d{10,15})\b")
        for i, block in enumerate(re.split(r"\n\s*\n", content.strip())):
            if not block.strip(): continue
            lines = block.strip().splitlines()
            name = lines[0].strip().lstrip("#").strip() or f"Case {i+1}"
            t = []; p = []; a = []
            for line in lines[1:]:
                ll = line.lower(); nums = num_re.findall(line)
                if ll.startswith("target"):       t.extend(nums)
                elif ll.startswith("participant"): p.extend(nums)
                a.extend(nums)
            self.cases.append({"case_id": f"CASE{i+1:04d}", "name": name,
                "targets": list(set(t)) or list(set(a)), "participants": list(set(p)),
                "all_numbers": list(set(a)), "raw": {"block": block}})

    @staticmethod
    def _norm(rec):
        def tl(v):
            if isinstance(v, list): return [str(x).strip() for x in v if str(x).strip()]
            if isinstance(v, str):  return [x.strip() for x in re.split(r"[;,\s]+", v)
                                             if re.match(r"^\d{7,15}$", x.strip())]
            return []
        t = tl(rec.get("targets", [])); p = tl(rec.get("participants", []))
        return {"case_id": rec.get("case_id", rec.get("id", "UNKNOWN")),
                "name": rec.get("name", rec.get("case_name", "UNKNOWN")),
                "targets": t, "participants": p, "all_numbers": list(set(t+p)), "raw": rec}

    def _build_index(self):
        self.number_index.clear()
        for c in self.cases:
            for n in c["all_numbers"]: self.number_index[n].append(c)

    def lookup(self, number, role=None):
        hits = self.number_index.get(number, [])
        if role == "target":      return [c for c in hits if number in c["targets"]]
        if role == "participant": return [c for c in hits if number in c["participants"]]
        return hits

_case_db = CaseDatabase()

def init_case_db(path=CASE_FILE_PATH):
    global _case_db; _case_db.load(path); return _case_db

# ─────────────────────────────────────────────────────────────────
#  LANGUAGE NORMALISERS
# ─────────────────────────────────────────────────────────────────
_HINDI_MAP = [
    (re.compile(r'\b(aane\s*wali|incoming\s*call|aai\s*hui)\b', re.I), 'incoming'),
    (re.compile(r'\b(jaane?\s*wali|outgoing\s*call|ki\s*gayi)\b', re.I), 'outgoing'),
    (re.compile(r'\b(dono|dono\s*taraf)\b', re.I), 'calls'),
    (re.compile(r'\b(sandesh|sandes|massage)\b', re.I), 'sms'),
    (re.compile(r'\b(awaaz|awaz|voice\s*call)\b', re.I), 'call'),
    (re.compile(r'\b(aaj|aaj\s*ka|aaj\s*ki)\b', re.I), 'today'),
    (re.compile(r'\b(kal|beeta\s*kal|guzra\s*kal)\b', re.I), 'yesterday'),
    (re.compile(r'\b(raat|raatko|raat\s*ko|raatri)\b', re.I), 'night'),
    (re.compile(r'\b(din\s*mein|din\s*ka|daytime|subah\s*se\s*shaam)\b', re.I), 'daytime'),
    (re.compile(r'\b(subah|savere|sabahe)\b', re.I), 'morning'),
    (re.compile(r'\b(shaam|sham|sanjh)\b', re.I), 'evening'),
    (re.compile(r'\b(dopahar|dupehr|dophar)\b', re.I), 'afternoon'),
    (re.compile(r'\b(pichhle?\s*(\d+)\s*din)\b', re.I), r'last \2 days'),
    (re.compile(r'\b(pichhla?\s*hafta|pichle\s*week)\b', re.I), 'last week'),
    (re.compile(r'\b(pichhla?\s*mahina|pichle\s*month)\b', re.I), 'last month'),
    (re.compile(r'\b(ke\s*baad|baad\s*mein)\b', re.I), 'after'),
    (re.compile(r'\b(se\s*pehle|pehle)\b', re.I), 'before'),
    (re.compile(r'\b(ke\s*beech|beech\s*mein|se\s*lekar)\b', re.I), 'between'),
    (re.compile(r'\b(dikhao|dikhaiye|batao|bataiye|dedo|dena|nikalo)\b', re.I), 'show'),
    (re.compile(r'\b(record|jaankari|vivran|vivarana|details?)\b', re.I), 'records'),
    (re.compile(r'\b(sabhi|saare|sab|tamam|poori?)\b', re.I), 'all'),
    (re.compile(r'\b(kitne|kitna|ginti)\b', re.I), 'count'),
    (re.compile(r'\b(lakshya|target\s*number)\b', re.I), 'target'),
    (re.compile(r'\b(ke\s*liye|ka|ki|ke)\b', re.I), 'of'),
    (re.compile(r'\b(wala|wali|waale)\b', re.I), ''),
    (re.compile(r'\b(pehli?\s*call|pehla?\s*call|shuruat\s*ki\s*call)\b', re.I), 'first call'),
    (re.compile(r'\b(aakhri?\s*call|antim\s*call|recent\s*call)\b', re.I), 'last call'),
    (re.compile(r'\b(pehle?\s*(din|week|mahina|month))\b', re.I), r'first \2'),
    (re.compile(r'\b(common|milte\s*jul[a-z]*|sanjha)\b', re.I), 'common'),
    (re.compile(r'\b(naye?\s*number|naya\s*contact)\b', re.I), 'new number'),
    (re.compile(r'\b(missing|gayab|na\s*mila)\b', re.I), 'missing'),
    (re.compile(r'\b(top\s*sampark|zyada\s*baat|adhik\s*sampark)\b', re.I), 'top contacts'),
    (re.compile(r'\b(sampark\s*suchi|contact\s*list)\b', re.I), 'contact list'),
    (re.compile(r'\b(b\s*party|dusra\s*number|doosra\s*number)\b', re.I), 'b party'),
    (re.compile(r'\b(tower|taavar|antena|mast)\b', re.I), 'tower'),
    (re.compile(r'\b(imei\s*suchi|imei\s*list|sabhi\s*imei)\b', re.I), 'all imei'),
    (re.compile(r'\b(imsi\s*suchi|imsi\s*list|sabhi\s*imsi)\b', re.I), 'all imsi'),
]
_DEVANAGARI_MAP = [
    ('कॉल','call'),('कॉल्स','calls'),('एसएमएस','sms'),('संदेश','sms'),
    ('आवाज','call'),('आने वाली','incoming'),('जाने वाली','outgoing'),
    ('आज','today'),('कल','yesterday'),('रात','night'),('दिन','daytime'),
    ('सुबह','morning'),('शाम','evening'),('दोपहर','afternoon'),
    ('पिछले','last'),('हफ्ते','week'),('महीने','month'),('दिनों','days'),
    ('दिखाओ','show'),('रिकॉर्ड','records'),('सभी','all'),
    ('पहली','first'),('आखिरी','last'),('सामान्य','common'),
    ('नया','new'),('नंबर','number'),('लक्ष्य','target'),('टॉवर','tower'),
    ('संपर्क','contact'),('सूची','list'),('शीर्ष','top'),
    ('के बाद','after'),('से पहले','before'),('के बीच','between'),
]
_TELUGU_MAP = [
    (re.compile(r'\b(yokka|lo|ki|ku|nu|tho|gurinchi|sambandhi)\b', re.I), 'of'),
    (re.compile(r'\b(anni|anni\s*ni)\b', re.I), 'all'),
    (re.compile(r'\b(vastunayi|vacchina|vastunnayi)\b', re.I), 'incoming'),
    (re.compile(r'\b(vellinayi|vellina|veltunnayi)\b', re.I), 'outgoing'),
    (re.compile(r'\b(sandeshalu|sandesam|messages?)\b', re.I), 'sms'),
    (re.compile(r'\b(callulu|matladuta|matladu)\b', re.I), 'calls'),
    (re.compile(r'\b(chupettu|chupu|chudu|chupiyyi|chupinchu)\b', re.I), 'show'),
    (re.compile(r'\b(ivvu|ivvandi|iyyi)\b', re.I), 'show'),
    (re.compile(r'\b(vivaram|vivara|details?|rekord[ulu]*)\b', re.I), 'records'),
    (re.compile(r'\b(enni|enta|lekka)\b', re.I), 'count'),
    (re.compile(r'\b(modhati|modati|mottamu|mowdati)\b', re.I), 'first'),
    (re.compile(r'\b(chivari|chivara|aakharu|aakhari)\b', re.I), 'last'),
    (re.compile(r'\b(ee\s*roju|indu|ee\s*roz)\b', re.I), 'today'),
    (re.compile(r'\b(ninna|ninadoo|manadina)\b', re.I), 'yesterday'),
    (re.compile(r'\b(raatri|ratri|rathri)\b', re.I), 'night'),
    (re.compile(r'\b(pakallu|pagalu|pakal)\b', re.I), 'daytime'),
    (re.compile(r'\b(saayantram|sayantram)\b', re.I), 'evening'),
    (re.compile(r'\b(madhyahnam|midday)\b', re.I), 'afternoon'),
    (re.compile(r'\b(tarvata|taruvata)\b', re.I), 'after'),
    (re.compile(r'\b(mundu|munupu)\b', re.I), 'before'),
    (re.compile(r'\b(common|polikalu|samana)\b', re.I), 'common'),
    (re.compile(r'\b(kotha|kotta|pudha|naveenam)\b', re.I), 'new'),
    (re.compile(r'\b(kotha\s*number|kotta\s*number|pudha\s*number)\b', re.I), 'new number'),
    (re.compile(r'\b(ledu\s*number|gurbuku\s*number|kalipoledu)\b', re.I), 'missing number'),
    (re.compile(r'\b(missing|ledu|kalipoledu|antapu)\b', re.I), 'missing'),
    (re.compile(r'\b(top\s*contacts?|ekkuva\s*calls?)\b', re.I), 'top contacts'),
    (re.compile(r'\b(b\s*party|maro\s*number)\b', re.I), 'b party'),
    (re.compile(r'\b(tower|tantu|antena)\b', re.I), 'tower'),
    (re.compile(r'\b(anni\s*imei|imei\s*list)\b', re.I), 'all imei'),
    (re.compile(r'\b(anni\s*imsi|imsi\s*list)\b', re.I), 'all imsi'),
    (re.compile(r'\b(number|nambaru|lakshyam)\b', re.I), 'target'),
]
_TELUGU_SCRIPT_MAP = [
    ('కాల్','call'),('కాల్స్','calls'),('ఎస్ఎంఎస్','sms'),
    ('సందేశాలు','sms'),('వచ్చిన','incoming'),('వెళ్ళిన','outgoing'),
    ('మొదటి','first'),('చివరి','last'),('చూపించు','show'),
    ('వివరాలు','records'),('అన్నీ','all'),('ఈ రోజు','today'),
    ('నిన్న','yesterday'),('రాత్రి','night'),('పగలు','daytime'),
    ('టవర్','tower'),('నంబర్','number'),('తర్వాత','after'),
    ('ముందు','before'),('మధ్య','between'),('కొత్త','new'),
    ('కొత్త నంబర్','new number'),('సాధారణ','common'),
]

def normalise_query(q):
    for te, en in sorted(_TELUGU_SCRIPT_MAP, key=lambda x: -len(x[0])): q = q.replace(te, en)
    for pat, repl in _TELUGU_MAP: q = pat.sub(repl, q)
    for hi, en in sorted(_DEVANAGARI_MAP, key=lambda x: -len(x[0])): q = q.replace(hi, en)
    for pat, repl in _HINDI_MAP: q = pat.sub(repl, q)
    return re.sub(r'  +', ' ', q).strip()

# ─────────────────────────────────────────────────────────────────
#  ALIAS NORMALISERS  (compiled once)
# ─────────────────────────────────────────────────────────────────
_BPARTY_ALIAS_RE     = re.compile(r'\b(b[_\-\s]?part(?:y|ie|ies)|bparty|bpty|b_party|B_Party)\b', re.I)
_PARTICIPANT_FUZZY_RE= re.compile(r'\bpart[a-z]*?i?c[a-z]*?p[a-z]*?a?n[a-z]*?t?s?\b', re.I)

def _pre_normalise(q):
    q = _BPARTY_ALIAS_RE.sub('b party', q)
    q = _PARTICIPANT_FUZZY_RE.sub('participant', q)
    return q

# ─────────────────────────────────────────────────────────────────
#  CASE NAME DETECTION
#  Matches tokens like: case342, case-342, case_342, case 342,
#                       Case342, CASE342, caseABC, case-abc1
# ─────────────────────────────────────────────────────────────────
_CASE_TOKEN_RE = re.compile(
    r'\b(case[\s_\-]?[a-zA-Z0-9][\w\-]*)\b', re.I)

def _extract_case_names(q: str) -> list:
    """
    Extract case name tokens from a query string.
    Returns normalised list (lowercase, spaces removed) e.g. ['case342','case11'].
    """
    raw = _CASE_TOKEN_RE.findall(q)
    # normalise: lowercase, strip inner spaces/dashes/underscores → 'case342'
    names = []
    for r in raw:
        n = re.sub(r'[\s_\-]+', '', r.lower())
        if n not in names:
            names.append(n)
    return names

def _has_case_name(q: str) -> bool:
    return bool(_CASE_TOKEN_RE.search(q))

# ─────────────────────────────────────────────────────────────────
#  FAST PARSER
# ─────────────────────────────────────────────────────────────────
_SMS_WORDS  = {"sms","message","messages","msg","msgs","text","texts","whatsapp",
               "sandesh","sandes","likhit"}
_CALL_WORDS = {"call","calls","voice","ring","dialled","dialed","awaaz","awaz","baat","kall"}
_OUT_WORDS  = {"out","outgoing","outbound","sent","made","dialled","dialed",
               "kiya","bheja","jane","jaane","ki"}
_IN_WORDS   = {"incoming","inbound","received","aaya","aayi","aane","mila","mili","prapt"}

_NIGHT_RE = re.compile(r'\b(night(?:\s*time)?|nighttime|raat|raatri)\b', re.I)
_DAY_RE   = re.compile(r'(?<!last\s)(?<!first\s)\b(day(?:\s*time)?|daytime|din\s*mein|din)\b', re.I)
_AFTER_TIME_RE   = re.compile(r'\b(?:after|ke\s*baad|baad)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)?(?:\s*hours?)?)\b', re.I)
_BEFORE_TIME_RE  = re.compile(r'\b(?:before|se\s*pehle|pehle)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)?(?:\s*hours?)?)\b', re.I)
_BETWEEN_TIME_RE = re.compile(r'\b(?:between|ke\s*beech)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)?)\s+(?:and|to|-|se)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)?)\b', re.I)
_FROM_TO_TIME_RE = re.compile(r'\b(?:from|se)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+(?:to|tak)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b', re.I)
_TODAY_RE    = re.compile(r'\b(today|aaj|aaj\s*ka|aaj\s*ki)\b', re.I)
_YEST_RE     = re.compile(r'\b(yesterday|kal|beeta\s*kal)\b', re.I)
_LASTND_RE   = re.compile(r'\b(?:last|pichhle?)\s+(?:(\d+)\s+)?(?:days?|din(?:on)?)\b', re.I)
_LASTWEEK_RE = re.compile(r'\b(?:last\s+week|pichhla?\s*hafta)\b', re.I)
_LASTMON_RE  = re.compile(r'\b(?:last\s+month|pichhla?\s*mahina)\b', re.I)
_NUM_RE      = re.compile(r'\b(\d{7,15})\b')
_PREFIX_RE   = re.compile(r'\bstart(?:s|ing)?\s+with\s+(\d+)', re.I)
_IMEI_RE     = re.compile(r'\b(\d{15})\b')
_BPARTY_RE   = re.compile(r'\bb[\s\-]?party\b', re.I)
_TOWER_RE    = re.compile(r'\b([A-Z0-9]{5,20})\b', re.I)
_TOWER_WORDS = {"tower","towerid","tower_id","cell","cellid","cgi","bts","site","first_cgi"}
_EXT_DATE_PATTERNS = [
    (re.compile(r'\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b'), 'yyyymmdd', False),
    (re.compile(r'\b(20\d{2})[/\-\.](0?[1-9]|1[0-2])[/\-\.](0?[1-9]|[12]\d|3[01])\b'), 'yyyy-mm-dd', False),
    (re.compile(r'\b(0?[1-9]|[12]\d|3[01])[/\-\.](0?[1-9]|1[0-2])[/\-\.](20\d{2})\b'), 'dd-mm-yyyy', True),
    (re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/](jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)[\s\-/,]*(\d{2,4})\b', re.I), 'natural_dmy', True),
    (re.compile(r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)[\s\-/,]+(\d{1,2})(?:st|nd|rd|th)?[\s\-/,]*(\d{2,4})\b', re.I), 'natural_mdy', False),
]
_TIME_RANGE_RE = re.compile(r'\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:to|-)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b', re.I)

def _hour_min(s):
    s = s.strip().lower().replace('hours', '').replace('hour', '').replace('baje', '').strip()
    m = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', s)
    if not m: return None, None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == 'pm' and h != 12: h += 12
    if ap == 'am' and h == 12: h = 0
    return h, mi

def _time_expr(mode, h1, h2=None):
    hf = {"$hour": "$SDateTime"}
    if mode == 'after':  return {"$expr": {"$gte": [hf, h1]}}
    if mode == 'before': return {"$expr": {"$lt":  [hf, h1]}}
    if h1 <= h2: return {"$expr": {"$and": [{"$gte": [hf, h1]}, {"$lte": [hf, h2]}]}}
    return {"$expr": {"$or": [{"$gte": [hf, h1]}, {"$lte": [hf, h2]}]}}

def _extract_date(q):
    for pat, fmt, df in _EXT_DATE_PATTERNS:
        m = pat.search(q)
        if not m: continue
        raw = m.group(0)
        try:
            if fmt in ('natural_dmy', 'natural_mdy'): dt = dateutil_parser.parse(raw, dayfirst=df)
            elif fmt == 'yyyymmdd':
                g = m.groups(); dt = dateutil_parser.parse(f"{g[0]}-{g[1]}-{g[2]}", dayfirst=False)
            elif fmt == 'yyyy-mm-dd':
                g = m.groups(); dt = dateutil_parser.parse(f"{g[0]}-{g[1].zfill(2)}-{g[2].zfill(2)}", dayfirst=False)
            else: dt = dateutil_parser.parse(raw, dayfirst=True)
            if not dt.tzinfo: dt = dt.replace(tzinfo=timezone.utc)
            return dt, m
        except: continue
    return None, None

def fast_parse_cdr(q):
    q   = normalise_query(q)
    _ql = q.lower()
    if any(kw in _ql for kw in ("new number","new numbers","missing number","missing numbers",
        "common b party","common contacts","common numbers",
        "kotha number","kotta number","ledu number","gurbuku number")):
        return None

    words = set(_ql.split()); qr = {}
    is_sms  = bool(words & _SMS_WORDS)
    is_call = bool(words & _CALL_WORDS)
    is_out  = bool(words & _OUT_WORDS)
    is_in   = bool(words & _IN_WORDS)

    if is_sms and not is_call:
        if is_out and not is_in:   qr["Call_Type"] = "SMS_OUT"
        elif is_in and not is_out: qr["Call_Type"] = "SMS_IN"
        else:                      qr["Call_Type"] = {"$in": ["SMS_IN", "SMS_OUT"]}
    elif is_call and not is_sms:
        if is_out and not is_in:   qr["Call_Type"] = "CALL_OUT"
        elif is_in and not is_out: qr["Call_Type"] = "CALL_IN"
        else:                      qr["Call_Type"] = {"$in": ["CALL_IN", "CALL_OUT"]}

    if words & _TOWER_WORDS:
        stop = {"tower","towerid","tower_id","cell","cellid","cgi","bts","site","first_cgi",
                "id","in","this","cdr","records","record","only","need","i","the","of","for","with"}
        for tok in _TOWER_RE.findall(q):
            if tok.lower() in stop or tok.isdigit() or re.match(r'^\d{15}$', tok): continue
            if re.search(r'[A-Za-z]', tok) and re.search(r'\d', tok):
                qr["First_CGI"] = tok; break

    im = _IMEI_RE.search(q)
    if im and len(im.group(1)) == 15: qr["IMEI"] = im.group(1)

    pm = _PREFIX_RE.search(q)
    if pm: qr["A_Party"] = {"$regex": f"^{pm.group(1)}"}; return qr

    nums = [n for n in _NUM_RE.findall(q) if len(n) < 15]
    if nums:
        if _BPARTY_RE.search(q): qr["B_Party"] = nums[0]
        else:                     qr["A_Party"] = nums[0]

    ts = False
    btw = _BETWEEN_TIME_RE.search(q) or _FROM_TO_TIME_RE.search(q)
    if btw:
        h1, _ = _hour_min(btw.group(1)); h2, _ = _hour_min(btw.group(2))
        if h1 is not None and h2 is not None: qr.update(_time_expr('between', h1, h2)); ts = True
    if not ts:
        af = _AFTER_TIME_RE.search(q)
        if af:
            h, _ = _hour_min(af.group(1))
            if h is not None: qr.update(_time_expr('after', h)); ts = True
    if not ts:
        bf = _BEFORE_TIME_RE.search(q)
        if bf:
            h, _ = _hour_min(bf.group(1))
            if h is not None: qr.update(_time_expr('before', h)); ts = True
    if not ts:
        if _NIGHT_RE.search(q):
            qr["$expr"] = {"$or": [{"$gte": [{"$hour": "$SDateTime"}, 18]},
                                    {"$lte": [{"$hour": "$SDateTime"}, 5]}]}; ts = True
        elif _DAY_RE.search(q):
            qr["$expr"] = {"$and": [{"$gte": [{"$hour": "$SDateTime"}, 6]},
                                     {"$lte": [{"$hour": "$SDateTime"}, 17]}]}; ts = True

    now = datetime.now(timezone.utc); today = now.date()
    if _TODAY_RE.search(q):
        s, e = _day_range(today); qr["SDateTime"] = {"$gte": s, "$lte": e}
    elif _YEST_RE.search(q):
        s, e = _day_range(today - timedelta(days=1)); qr["SDateTime"] = {"$gte": s, "$lte": e}
    elif _LASTWEEK_RE.search(q):
        qr["SDateTime"] = {
            "$gte": datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(days=7),
            "$lte": datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc)}
    elif _LASTMON_RE.search(q):
        qr["SDateTime"] = {
            "$gte": datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(days=30),
            "$lte": datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc)}
    else:
        nd = _LASTND_RE.search(q)
        if nd:
            n = int(nd.group(1)) if nd.group(1) else 1
            qr["SDateTime"] = {
                "$gte": datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - timedelta(days=n),
                "$lte": datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc)}
        else:
            dr, _ = _extract_date(q)
            if dr:
                s, e = _day_range(dr.date())
                tr = _TIME_RANGE_RE.search(q)
                if tr:
                    h1, m1 = _hour_min(tr.group(1)); h2, m2 = _hour_min(tr.group(2))
                    if h1 is not None:
                        s = s.replace(hour=h1, minute=m1)
                        e = e.replace(hour=h2, minute=m2, second=0)
                qr["SDateTime"] = {"$gte": s, "$lte": e}

    if any(k in qr for k in ("A_Party","B_Party","IMEI","First_CGI","$expr","SDateTime","IMSI")):
        return qr
    return None

# ─────────────────────────────────────────────────────────────────
#  LLM QUERY BUILDER
# ─────────────────────────────────────────────────────────────────
def llm_to_query(user_query):
    user_query = normalise_query(user_query)
    text = _llm_safe([{"role": "system", "content": CDR_SYSTEM_PROMPT},
                      {"role": "user",   "content": user_query}])
    if text is None:
        raise ValueError(f"LLM timeout {LLM_TIMEOUT_QUERY}s. Try a simpler query.")
    clean = re.sub(r"```json|```", "", text).strip()
    m = re.search(r"\{.*\}", clean, re.DOTALL)
    if m: clean = m.group(0)
    return force_dates(json.loads(clean))

def build_query(user_query):
    user_query = normalise_query(user_query)
    fast = fast_parse_cdr(user_query)
    if fast is not None: print(f"[Parser] FAST | {fast}"); return fast, "fast"
    print(f"[Parser] LLM fallback: {user_query}")
    return llm_to_query(user_query), "llm"

# ─────────────────────────────────────────────────────────────────
#  CDR QUERY
# ─────────────────────────────────────────────────────────────────
CDR_PROJECTION = {
    "_id":1,"A_Party":1,"B_Party":1,"SDateTime":1,"EDateTime":1,
    "SDate":1,"STime":1,"Duration":1,"Call_Type":1,"FileCallType":1,
    "Con_Type":1,"First_CGI":1,"IMEI":1,"IMSI":1,"IMEI_TAC":1,"IMSI_CODE":1,"seq_id":1,
}

def run_cdr_query(user_query, limit=MAX_RECORDS,
                  mongo_uri="mongodb://localhost:27017/", raw_mode=False):
    try:
        t0 = time.time(); query, parser_used = build_query(user_query); t_parse = round(time.time()-t0, 3)
        print(f"[CDR] parser={parser_used} {t_parse}s | {query}")
        col = get_collection("CDR", "CallDetailRecords", mongo_uri)
        hint = pick_cdr_hint(query); idx_q, flt_q = split_query(query, CDR_NON_INDEXED)
        try: _memory_guard(col, idx_q, hint, limit)
        except MemoryError as me: return {"status": "error", "message": str(me)}
        pipeline = [{"$match": idx_q}]
        if flt_q: pipeline.append({"$match": flt_q})
        pipeline += [{"$sort": {"SDateTime": 1}}, {"$limit": limit}, {"$project": CDR_PROJECTION}]
        t1 = time.time(); raw = _agg_safe(col, pipeline, hint, limit); t_db = round(time.time()-t1, 3)
        print(f"[CDR] DB={t_db}s docs={len(raw)} hint={hint}")
        if not raw:
            return {"status":"empty","query":str(query),"count":0,"records":[],
                    "timing":{"parse_s":t_parse,"db_s":t_db,"parser":parser_used}}
        records = [serialize_doc(r) for r in raw]
        te = time.time(); enrich_records_with_case(records, mongo_uri); t_enrich = round(time.time()-te, 3)
        print(f"[Enrich] {t_enrich}s")
        all_cases = {}
        for rec in records:
            for n in (rec.get("case_names") or "").split(" , "):
                if n: all_cases[n] = all_cases.get(n, 0) + 1
        case_summary = {"unique_cases": len(all_cases), "case_hit_count": all_cases}
        base = {"status":"success","query":str(query),"hint_used":hint or "auto",
                "parser":parser_used,"count":len(records),"records":records,
                "case_summary":case_summary,
                "timing":{"parse_s":t_parse,"db_s":t_db,"enrich_s":t_enrich,"parser":parser_used}}
        if raw_mode: return base
        analytics = _cdr_analytics(col, hint, idx_q, flt_q, limit)
        #return {**base, "summary": ai_summary(analytics), "analytics": analytics}
        return {**base, "summary": ai_summary(analytics), "analytics": records, "records": [analytics]}
    except ValueError as e:        return {"status":"error","message":str(e)}
    except json.JSONDecodeError as e: return {"status":"error","message":f"LLM invalid JSON: {e}"}
    except MemoryError as e:        return {"status":"error","message":str(e)}
    except Exception as e:          return {"status":"error","message":str(e),"trace":traceback.format_exc()}

# ─────────────────────────────────────────────────────────────────
#  ANALYTICS  (daily + weekly + monthly distributions)
# ─────────────────────────────────────────────────────────────────
def _cdr_analytics(col, hint, idx_q, flt_q, limit):
    base = [{"$match": idx_q}]
    if flt_q: base.append({"$match": flt_q})
    base.append({"$limit": MAX_AGG_DOCS})
    try:
        r = _agg_safe(col, base + [{"$facet": {
            "call_types": [{"$group":{"_id":"$Call_Type","count":{"$sum":1}}},{"$sort":{"count":-1}}],
            "b_party_detail": [
                {"$group":{"_id":"$B_Party","total":{"$sum":1},"dur":{"$sum":"$Duration"},
                    "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
                    "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
                    "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
                    "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
                    "first_seen":{"$min":"$SDateTime"},"last_seen":{"$max":"$SDateTime"}}},
                {"$sort":{"total":-1}},{"$limit":20}],
            "top_towers": [{"$group":{"_id":"$First_CGI","count":{"$sum":1}}},
                           {"$sort":{"count":-1}},{"$limit":5}],
            "hourly":  [{"$group":{"_id":{"$hour":"$SDateTime"},"count":{"$sum":1}}},{"$sort":{"_id":1}}],
            "daily":   [{"$group":{"_id":{"$dateToString":{"format":"%Y-%m-%d","date":"$SDateTime"}},
                            "count":{"$sum":1},"total_dur":{"$sum":"$Duration"},
                            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
                            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
                            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
                            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}}}},
                        {"$sort":{"_id":1}}],
            "weekly":  [{"$group":{"_id":{"$dateToString":{"format":"%G-W%V","date":"$SDateTime"}},
                            "count":{"$sum":1},"total_dur":{"$sum":"$Duration"},
                            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
                            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
                            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
                            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}}}},
                        {"$sort":{"_id":1}}],
            "monthly": [{"$group":{"_id":{"$dateToString":{"format":"%Y-%m","date":"$SDateTime"}},
                            "count":{"$sum":1},"total_dur":{"$sum":"$Duration"},
                            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
                            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
                            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
                            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}}}},
                        {"$sort":{"_id":1}}],
            "stats": [{"$group":{"_id":None,"total":{"$sum":"$Duration"},"avg":{"$avg":"$Duration"},
                                 "max":{"$max":"$Duration"},"count":{"$sum":1},
                                 "unique_b":{"$addToSet":"$B_Party"}}}],
            "imeis": [{"$group":{"_id":"$IMEI","count":{"$sum":1}}},
                      {"$sort":{"count":-1}},{"$limit":10}],
        }}], hint, limit=1)
    except Exception as e:
        print(f"[Analytics] error: {e}"); return {}
    if not r: return {}
    r = r[0]; s = r["stats"][0] if r["stats"] else {}; total_dur = int(s.get("total", 0) or 0)

    def _dr(x):
        return {"calls": x["count"], "duration": secs_human(x["total_dur"]),
                "call_out": x["call_out"], "call_in": x["call_in"],
                "sms_out": x["sms_out"], "sms_in": x["sms_in"]}

    b_contacts = [
        {"number": x["_id"], "total": x["total"], "call_out": x["call_out"],
         "call_in": x["call_in"], "sms_out": x["sms_out"], "sms_in": x["sms_in"],
         "duration": secs_human(int(x["dur"] or 0)),
         "first_seen": x["first_seen"].isoformat() if isinstance(x.get("first_seen"), datetime) else str(x.get("first_seen","")),
         "last_seen":  x["last_seen"].isoformat()  if isinstance(x.get("last_seen"),  datetime) else str(x.get("last_seen", ""))}
        for x in r["b_party_detail"] if x["_id"]
    ]
    return {
        "total_records":          int(s.get("count", 0) or 0),
        "unique_contacts":        len(s.get("unique_b", []) or []),
        "call_type_breakdown":   {x["_id"]: x["count"] for x in r["call_types"] if x["_id"]},#,[(x["_id"], x["count"]) for x in r["call_types"] if x["_id"]]
        "top_contacts":           [f'{x["number"]} ({x["total"]})' for x in b_contacts[:10]],#f'{x["number"]} ({x["total"]})'
        "b_party_contacts":       b_contacts,
        "top_towers":             [f'{x["_id"]} ({x["count"]})' for x in r["top_towers"] if x["_id"]],
        "hourly_distribution":    {x["_id"]: x["count"] for x in r["hourly"]},
        "daily_distribution":     {x["_id"]: _dr(x) for x in r["daily"]},
        "weekly_distribution":    {x["_id"]: _dr(x) for x in r["weekly"]},
        "monthly_distribution":   {x["_id"]: _dr(x) for x in r["monthly"]},
        "total_duration_seconds": total_dur,
        "total_duration_human":   secs_human(total_dur),
        "avg_duration_seconds":   int(s.get("avg", 0) or 0),
        "max_duration_human":     secs_human(s.get("max", 0) or 0),
        "imei_list":              " , ".join([x["_id"] for x in r["imeis"] if x["_id"]]),
    }

def ai_summary(analytics):
    if not analytics: return "No data."
    snap = {k: v for k, v in analytics.items()
            if k not in ("imei_list","daily_distribution","b_party_contacts",
                         "weekly_distribution","monthly_distribution")}
    return _llm_stream_summary([{"role":"system","content":SUMMARY_PROMPT},
                                {"role":"user","content":f"CDR:\n{json.dumps(snap,default=str)}"}])

# ─────────────────────────────────────────────────────────────────
#  DISTRIBUTION-ONLY QUERIES  (by number or by case)
# ─────────────────────────────────────────────────────────────────
def query_distribution(target, dist_type, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    fmt = {"daily":"%Y-%m-%d","weekly":"%G-W%V","monthly":"%Y-%m"}.get(dist_type,"%Y-%m-%d")
    rows = _agg_safe(col, [
        {"$match": {"A_Party": target}},
        {"$group": {"_id": {"$dateToString":{"format":fmt,"date":"$SDateTime"}},
            "count":{"$sum":1},"total_dur":{"$sum":"$Duration"},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}}}},
        {"$sort": {"_id": 1}},
    ], hint="A_Party_1", limit=MAX_RECORDS)
    if not rows:
        return {"status":"empty","query_type":f"{dist_type}_distribution","target":target,
                "count":0,"records":[],"summary":f"No records for {target}.","analytics":{}}
    records = [
        {"_label": r["_id"], f"{dist_type}_period": r["_id"], "A_Party": target,
         "total_calls": r["count"], "call_out": r["call_out"], "call_in": r["call_in"],
         "sms_out": r["sms_out"], "sms_in": r["sms_in"],
         "duration": secs_human(int(r.get("total_dur") or 0))}
        for r in rows
    ]
    total = sum(r["total_calls"] for r in records)
    summary = "\n".join(
        [f"{dist_type.capitalize()} distribution for {target}:",
         f"  Periods: {len(records)}  Total: {total}", ""] +
        [f"  {r['_label']:>12}  calls={r['total_calls']:>5}  "
         f"(cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']})  "
         f"dur={r['duration']}" for r in records]
    )
    return {"status":"success","query_type":f"{dist_type}_distribution","target":target,
            "count":len(records),"records":records,"summary":summary,
            "hint_used":"A_Party_1","parser":"special",
            "analytics":{f"{dist_type}_distribution":{r["_label"]:r for r in records},
                         "total_records":total,"period_count":len(records)},
            "timing":{"db_s":round(time.time()-t0,3),"parse_s":0.0,"parser":"special"}}

# ─────────────────────────────────────────────────────────────────
#  ══════════════════  CASE-NAME QUERY ENGINE  ════════════════════
#  All functions below resolve case names → seq_ids → CDR records
#  and then reuse existing CDR logic on those targets.
# ─────────────────────────────────────────────────────────────────

def _empty_case_result(qtype, case_names, msg):
    return {"status":"empty","query_type":qtype,"case_names":case_names,
            "count":0,"records":[],"case_summary":{},"summary":msg,
            "timing":{"db_s":0,"parse_s":0,"parser":"case_name"}}

def _get_seq_ids_for_cases(case_names, mongo_uri):
    """Returns (seq_ids_list, error_result_or_None)."""
    case_to_seqids = _resolve_case_names_to_seq_ids(case_names, mongo_uri)
    if not case_to_seqids:
        msg = f"No CrimeRegistry entries found for: {', '.join(case_names)}"
        return None, _empty_case_result("case_cdr", case_names, msg)
    all_seq_ids = list({s for seqs in case_to_seqids.values() for s in seqs})
    if not all_seq_ids:
        msg = f"Cases found in CrimeRegistry but no DataNexus links: {', '.join(case_names)}"
        return None, _empty_case_result("case_cdr", case_names, msg)
    return all_seq_ids, None


def query_case_cdr(case_names: list, limit=MAX_RECORDS,
                   mongo_uri="mongodb://localhost:27017/", raw_mode=False):
    """
    Core: fetch all CDR records linked to given case name(s).
    Supports: "show CDR of case342", "all records of case11 and case22"
    """
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return err

    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    pipeline = [
        {"$match":   {"seq_id": {"$in": seq_ids}}},
        {"$sort":    {"SDateTime": 1}},
        {"$limit":   limit},
        {"$project": CDR_PROJECTION},
    ]
    raw = _agg_safe(col, pipeline, hint="seq_id_1", limit=limit)
    t_db = round(time.time()-t0, 3)

    if not raw:
        return _empty_case_result("case_cdr", case_names,
                                  f"No CDR records found for case(s): {', '.join(case_names)}")

    records = [serialize_doc(r) for r in raw]
    te = time.time(); enrich_records_with_case(records, mongo_uri); t_enrich = round(time.time()-te, 3)

    all_cases_map = {}
    for rec in records:
        for n in (rec.get("case_names") or "").split(" , "):
            if n: all_cases_map[n] = all_cases_map.get(n, 0) + 1
    case_summary = {"unique_cases": len(all_cases_map), "case_hit_count": all_cases_map}

    base = {"status":"success","query_type":"case_cdr","case_names":case_names,
            "query":f"seq_id $in {len(seq_ids)} ids","hint_used":"seq_id_1","parser":"case_name",
            "count":len(records),"records":records,"case_summary":case_summary,
            "timing":{"db_s":t_db,"enrich_s":t_enrich,"parse_s":0,"parser":"case_name"}}

    if raw_mode: return base

    # Build analytics inline (reuse idx_q trick)
    idx_q = {"seq_id": {"$in": seq_ids}}
    analytics = _cdr_analytics(col, "seq_id_1", idx_q, {}, limit)
    return {**base, "summary": ai_summary(analytics), "analytics": analytics}


def query_case_imei(case_names: list, mongo_uri="mongodb://localhost:27017/"):
    """All IMEIs used in given case(s)."""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":"case_imei"}

    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"seq_id": {"$in": seq_ids},
                    "IMEI": {"$exists":True,"$ne":None,"$ne":""}}},
        {"$group": {"_id":"$IMEI","count":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},
            "first_seen":{"$min":"$SDateTime"},"last_seen":{"$max":"$SDateTime"},
            "targets":{"$addToSet":"$A_Party"},"imsi_set":{"$addToSet":"$IMSI"}}},
        {"$sort": {"count":-1}},
    ], hint="seq_id_1")
    elapsed = round(time.time()-t0, 3)

    if not rows:
        return _empty_case_result("case_imei", case_names,
                                  f"No IMEI records for case(s): {', '.join(case_names)}")

    records = [
        {"_label": f"IMEI #{i+1}", "IMEI": r["_id"],
         "IMEI_TAC": r["_id"][:8] if r["_id"] and len(r["_id"])>=8 else r["_id"],
         "count": r["count"], "call_out": r["call_out"], "call_in": r["call_in"],
         "sms_out": r["sms_out"], "sms_in": r["sms_in"],
         "duration": secs_human(int(r.get("duration") or 0)),
         "first_seen": _fmt(r.get("first_seen")), "last_seen": _fmt(r.get("last_seen")),
         "targets_used": " , ".join([x for x in r.get("targets",[]) if x]),
         "imsi_used":    " , ".join([x for x in r.get("imsi_set",[]) if x])}for i, r in enumerate(rows) if r["_id"]]
    summary = "\n".join(
        [f"Case(s) {', '.join(case_names)} — {len(records)} distinct IMEI(s):"] +
        [f"  [{i+1}] {r['IMEI']}  |  {r['count']} records  |  {r['first_seen']} → {r['last_seen']}"
         for i, r in enumerate(records)]
    )
    return {"status":"success","query_type":"case_imei","case_names":case_names,
            "count":len(records),"records":records,"summary":summary,
            "hint_used":"seq_id_1","parser":"case_name",
            "analytics":{"total_imei_count":len(records),
                         "imei_list":" , ".join([r["IMEI"] for r in records])},
            "case_summary":{"unique_cases":len(case_names),"case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}


def query_case_imsi(case_names: list, mongo_uri="mongodb://localhost:27017/"):
    """All IMSIs used in given case(s)."""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":"case_imsi"}

    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"seq_id": {"$in": seq_ids},
                    "IMSI": {"$exists":True,"$ne":None,"$ne":""}}},
        {"$group": {"_id":"$IMSI","count":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},
            "first_seen":{"$min":"$SDateTime"},"last_seen":{"$max":"$SDateTime"},
            "targets":{"$addToSet":"$A_Party"},"imei_set":{"$addToSet":"$IMEI"}}},
        {"$sort": {"count":-1}},
    ], hint="seq_id_1")
    elapsed = round(time.time()-t0, 3)

    if not rows:
        return _empty_case_result("case_imsi", case_names,
                                  f"No IMSI records for case(s): {', '.join(case_names)}")

    records = [
        {"_label": f"IMSI #{i+1}", "IMSI": r["_id"],
         "count": r["count"], "call_out": r["call_out"], "call_in": r["call_in"],
         "sms_out": r["sms_out"], "sms_in": r["sms_in"],
         "duration": secs_human(int(r.get("duration") or 0)),
         "first_seen": _fmt(r.get("first_seen")), "last_seen": _fmt(r.get("last_seen")),
         "targets_used": " , ".join([x for x in r.get("targets",[]) if x]),
         "imei_used":    " , ".join([x for x in r.get("imei_set",[]) if x])}
        for i, r in enumerate(rows) if r["_id"]
    ]
    summary = "\n".join(
        [f"Case(s) {', '.join(case_names)} — {len(records)} distinct IMSI(s):"] +
        [f"  [{i+1}] {r['IMSI']}  |  {r['count']} records  |  {r['first_seen']} → {r['last_seen']}"
         for i, r in enumerate(records)]
    )
    return {"status":"success","query_type":"case_imsi","case_names":case_names,
            "count":len(records),"records":records,"summary":summary,
            "hint_used":"seq_id_1","parser":"case_name",
            "analytics":{"total_imsi_count":len(records),
                         "imsi_list":" , ".join([r["IMSI"] for r in records])},
            "case_summary":{"unique_cases":len(case_names),"case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}


def query_case_common_bparty(case_names: list, mongo_uri="mongodb://localhost:27017/"):
    """Common B-party numbers across given case(s)."""
    t0 = time.time()
    # Resolve each case to its targets, then find common B-parties
    case_to_seqids = _resolve_case_names_to_seq_ids(case_names, mongo_uri)
    if not case_to_seqids:
        return _empty_case_result("case_common_bparty", case_names,
                                  f"No registry entries for: {', '.join(case_names)}")

    col = get_collection("CDR","CallDetailRecords",mongo_uri)

    def _bparty_for_seqids(seq_ids):
        rows = _agg_safe(col, [
            {"$match": {"seq_id": {"$in": seq_ids}}},
            {"$group": {"_id":"$B_Party","total":{"$sum":1},"duration":{"$sum":"$Duration"},
                "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
                "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
                "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
                "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
                "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}},
        ], hint="seq_id_1", limit=100000)
        return {r["_id"]: r for r in rows if r["_id"]}

    with ThreadPoolExecutor(max_workers=min(len(case_to_seqids), 8)) as ex:
        bp_maps = list(ex.map(lambda kv: _bparty_for_seqids(kv[1]), case_to_seqids.items()))
    case_list = list(case_to_seqids.keys())
    bp_map = dict(zip(case_list, bp_maps))

    all_nums = set()
    for v in bp_map.values(): all_nums.update(v.keys())

    records = []
    for num in all_nums:
        matched = [c for c in case_list if num in bp_map[c]]
        if len(matched) < 2 and len(case_names) > 1: continue
        row = {"Number": num, "Count": len(matched), "Cases": " & ".join(matched)}
        firsts, lasts = [], []
        for c in case_list:
            if c in matched:
                d = bp_map[c][num]; row[c] = "YES"
                f, l = _utc(d.get("first")), _utc(d.get("last"))
                if f: firsts.append(f)
                if l: lasts.append(l)
            else:
                row[c] = "-"
        row["First & Last"] = f"{_fmt(min(firsts))} - {_fmt(max(lasts))}" if firsts else "-"
        records.append(row)
    records.sort(key=lambda x: (-x["Count"], x["Number"]))

    summary = (f"Common B-party numbers across case(s) {', '.join(case_names)}: "
               f"{len(records)} found")
    return {"status":"success","query_type":"case_common_bparty","case_names":case_names,
            "count":len(records),"records":records,"summary":summary,
            "case_summary":{"unique_cases":len(case_names),"case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":round(time.time()-t0,3),"parse_s":0,"parser":"case_name"}}


def query_case_top_contacts(case_names: list, n=20, mongo_uri="mongodb://localhost:27017/"):
    """Top B-party contacts across case(s)."""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":"case_top_contacts"}

    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"seq_id": {"$in": seq_ids}}},
        {"$group": {"_id":"$B_Party","total":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},
            "first_seen":{"$min":"$SDateTime"},"last_seen":{"$max":"$SDateTime"}}},
        {"$sort": {"total":-1}},{"$limit": n},
    ], hint="seq_id_1")
    elapsed = round(time.time()-t0, 3)
    if not rows:
        return _empty_case_result("case_top_contacts", case_names,
                                  f"No contact data for case(s): {', '.join(case_names)}")
    records = [
        {"_label": f"#{rank}", "rank": rank, "B_Party": r["_id"], "total": r["total"],
         "call_out": r["call_out"], "call_in": r["call_in"],
         "sms_out": r["sms_out"], "sms_in": r["sms_in"],
         "duration": secs_human(int(r.get("duration") or 0)),
         "first_seen": _fmt(r.get("first_seen")), "last_seen": _fmt(r.get("last_seen"))}
        for rank, r in enumerate(rows, 1) if r["_id"]
    ]
    total_i = sum(r["total"] for r in records)
    summary = "\n".join(
        [f"Top {len(records)} contacts for case(s) {', '.join(case_names)}:",
         f"  Total interactions: {total_i}", ""] +
        [f"  #{r['rank']:>2}  {r['B_Party']:<15}  {r['total']:>5}  "
         f"(cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']})  "
         f"{r['first_seen']} → {r['last_seen']}" for r in records]
    )
    return {"status":"success","query_type":"case_top_contacts","case_names":case_names,"n":n,
            "count":len(records),"records":records,"summary":summary,
            "hint_used":"seq_id_1","parser":"case_name",
            "analytics":{"total_interactions":total_i,"contact_count":len(records)},
            "case_summary":{"unique_cases":len(case_names),"case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}


def query_case_frequent_callers(case_names: list, n=20, mongo_uri="mongodb://localhost:27017/"):
    """Numbers that called targets in given case(s)."""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":"case_frequent_callers"}

    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    # Get targets first
    target_rows = _agg_safe(col, [
        {"$match": {"seq_id": {"$in": seq_ids}}},
        {"$group": {"_id": "$A_Party"}},
    ], hint="seq_id_1", limit=10000)
    targets = [r["_id"] for r in target_rows if r.get("_id")]

    if not targets:
        return _empty_case_result("case_frequent_callers", case_names,
                                  f"No targets in case(s): {', '.join(case_names)}")

    rows = _agg_safe(col, [
        {"$match": {"B_Party": {"$in": targets}}},
        {"$group": {"_id":"$A_Party","total":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},
            "first_seen":{"$min":"$SDateTime"},"last_seen":{"$max":"$SDateTime"}}},
        {"$sort": {"total":-1}},{"$limit": n},
    ])
    elapsed = round(time.time()-t0, 3)
    if not rows:
        return _empty_case_result("case_frequent_callers", case_names,
                                  f"No callers for case(s): {', '.join(case_names)}")
    records = [
        {"_label": f"#{rank}", "rank": rank, "A_Party": r["_id"], "total": r["total"],
         "call_out": r["call_out"], "call_in": r["call_in"],
         "sms_out": r["sms_out"], "sms_in": r["sms_in"],
         "duration": secs_human(int(r.get("duration") or 0)),
         "first_seen": _fmt(r.get("first_seen")), "last_seen": _fmt(r.get("last_seen"))}
        for rank, r in enumerate(rows, 1) if r["_id"]
    ]
    return {"status":"success","query_type":"case_frequent_callers","case_names":case_names,
            "count":len(records),"records":records,
            "summary":f"Top {len(records)} callers for case(s) {', '.join(case_names)}",
            "case_summary":{"unique_cases":len(case_names),"case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}


def query_case_first_last(case_names: list, mode="both",
                          mongo_uri="mongodb://localhost:27017/"):
    """First / last / both calls for targets in given case(s). mode: first|last|both"""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":"case_first_last_call"}

    col = get_collection("CDR","CallDetailRecords",mongo_uri); p = _proj()
    facets = {}
    if mode in ("first","both"):
        facets["first_call"] = [{"$match":{"seq_id":{"$in":seq_ids}}},
                                 {"$sort":{"SDateTime":1}},{"$limit":1},{"$project":p}]
    if mode in ("last","both"):
        facets["last_call"]  = [{"$match":{"seq_id":{"$in":seq_ids}}},
                                 {"$sort":{"SDateTime":-1}},{"$limit":1},{"$project":p}]
    facets["stats"] = [{"$match":{"seq_id":{"$in":seq_ids}}},
                       {"$group":{"_id":None,"total":{"$sum":1},
                                  "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}}]

    r = _agg_safe(col, [{"$match":{"seq_id":{"$in":seq_ids}}},{"$facet":facets}],
                  hint="seq_id_1", limit=1)
    elapsed = round(time.time()-t0, 3)

    if not r: return _empty_case_result("case_first_last_call", case_names, "No records.")
    r = r[0]; stats = r.get("stats",[{}])[0] if r.get("stats") else {}
    fd = _utc(stats.get("first")); ld = _utc(stats.get("last"))
    span = (ld-fd).days if fd and ld else None

    records = []
    if "first_call" in r and r["first_call"]:
        doc = serialize_doc(r["first_call"][0]); doc["_label"]="FIRST CALL"; records.append(doc)
    if "last_call" in r and r["last_call"]:
        doc = serialize_doc(r["last_call"][0])
        if not records or doc.get("_id") != records[0].get("_id"):
            doc["_label"]="LAST CALL"; records.append(doc)

    result = {"status":"success" if records else "empty",
              "query_type":"case_first_last_call","case_names":case_names,
              "count":len(records),"records":records,
              "summary":(f"First call: {_fmt(fd)}\nLast call: {_fmt(ld)}\n"
                         f"Total records: {stats.get('total',0)}\nSpan: {span} days")
                        if records else "No records.",
              "analytics":{"total_records":int(stats.get("total",0)),
                           "first_call_datetime":_fmt(fd),"last_call_datetime":_fmt(ld),
                           "active_span_days":span},
              "case_summary":{"unique_cases":len(case_names),
                              "case_hit_count":{c:1 for c in case_names}},
              "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}
    return _enrich_and_summarise(result, mongo_uri)


def query_case_distribution(case_names: list, dist_type: str,
                            mongo_uri="mongodb://localhost:27017/"):
    """Daily/weekly/monthly distribution for case(s)."""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":f"case_{dist_type}_distribution"}

    fmt = {"daily":"%Y-%m-%d","weekly":"%G-W%V","monthly":"%Y-%m"}.get(dist_type, "%Y-%m-%d")
    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"seq_id": {"$in": seq_ids}}},
        {"$group": {"_id":{"$dateToString":{"format":fmt,"date":"$SDateTime"}},
            "count":{"$sum":1},"total_dur":{"$sum":"$Duration"},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}}}},
        {"$sort": {"_id": 1}},
    ], hint="seq_id_1", limit=MAX_RECORDS)
    elapsed = round(time.time()-t0, 3)

    if not rows:
        return _empty_case_result(f"case_{dist_type}_distribution", case_names,
                                  f"No records for case(s): {', '.join(case_names)}")
    records = [
        {"_label": r["_id"], f"{dist_type}_period": r["_id"],
         "case_names": ", ".join(case_names), "total_calls": r["count"],
         "call_out": r["call_out"], "call_in": r["call_in"],
         "sms_out": r["sms_out"], "sms_in": r["sms_in"],
         "duration": secs_human(int(r.get("total_dur") or 0))}
        for r in rows
    ]
    total = sum(r["total_calls"] for r in records)
    summary = "\n".join(
        [f"{dist_type.capitalize()} distribution for case(s) {', '.join(case_names)}:",
         f"  Periods: {len(records)}  Total: {total}", ""] +
        [f"  {r['_label']:>12}  calls={r['total_calls']:>5}  "
         f"(cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']})  "
         f"dur={r['duration']}" for r in records]
    )
    return {"status":"success","query_type":f"case_{dist_type}_distribution",
            "case_names":case_names,"count":len(records),"records":records,"summary":summary,
            "hint_used":"seq_id_1","parser":"case_name",
            "analytics":{"total_records":total,"period_count":len(records)},
            "case_summary":{"unique_cases":len(case_names),
                            "case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}


def query_case_targets(case_names: list, mongo_uri="mongodb://localhost:27017/"):
    """List all A_Party (target) numbers in given case(s)."""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":"case_targets"}

    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"seq_id": {"$in": seq_ids}}},
        {"$group": {"_id":"$A_Party","count":{"$sum":1},
                    "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}},
        {"$sort": {"count":-1}},
    ], hint="seq_id_1", limit=10000)
    elapsed = round(time.time()-t0, 3)

    if not rows:
        return _empty_case_result("case_targets", case_names,
                                  f"No targets for case(s): {', '.join(case_names)}")
    records = [
        {"_label": f"Target #{i+1}", "A_Party": r["_id"], "total_records": r["count"],
         "first_seen": _fmt(r.get("first")), "last_seen": _fmt(r.get("last"))}
        for i, r in enumerate(rows) if r["_id"]
    ]
    summary = (f"Case(s) {', '.join(case_names)} — {len(records)} target number(s):\n" +
               "\n".join(f"  [{i+1}] {r['A_Party']}  |  {r['total_records']} records  "
                         f"|  {r['first_seen']} → {r['last_seen']}"
                         for i, r in enumerate(records)))
    return {"status":"success","query_type":"case_targets","case_names": case_names,
            "count":len(records),"records":records,"summary":summary,
            "hint_used":"seq_id_1","parser":"case_name",
            "analytics":{"target_count":len(records),"targets":[r["A_Party"] for r in records]},
            "case_summary":{"unique_cases":len(case_names),
                            "case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}


def query_case_towers(case_names: list, mongo_uri="mongodb://localhost:27017/"):
    """Top towers used in given case(s)."""
    t0 = time.time()
    seq_ids, err = _get_seq_ids_for_cases(case_names, mongo_uri)
    if err: return {**err, "query_type":"case_towers"}

    col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"seq_id": {"$in": seq_ids},
                    "First_CGI": {"$exists":True,"$ne":None,"$ne":""}}},
        {"$group": {"_id":"$First_CGI","count":{"$sum":1},
                    "targets":{"$addToSet":"$A_Party"},
                    "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}},
        {"$sort": {"count":-1}},
    ], hint="seq_id_1", limit=200)
    elapsed = round(time.time()-t0, 3)

    if not rows:
        return _empty_case_result("case_towers", case_names,
                                  f"No tower data for case(s): {', '.join(case_names)}")
    records = [
        {"_label": f"Tower #{i+1}", "First_CGI": r["_id"], "count": r["count"],
         "targets":" , ".join( [x for x in r.get("targets",[]) if x]),
         "first_seen": _fmt(r.get("first")), "last_seen": _fmt(r.get("last"))}
        for i, r in enumerate(rows) if r["_id"]
    ]
    summary = (f"Case(s) {', '.join(case_names)} — {len(records)} tower(s):\n" +
               "\n".join(f"  [{i+1}] {r['First_CGI']}  |  {r['count']} hits  "
                         f"|  {r['first_seen']} → {r['last_seen']}"
                         for i, r in enumerate(records[:20])))
    return {"status":"success","query_type":"case_towers","case_names":case_names,
            "count":len(records),"records":records,"summary":summary,
            "hint_used":"seq_id_1","parser":"case_name",
            "analytics":{"tower_count":len(records)},
            "case_summary":{"unique_cases":len(case_names),
                            "case_hit_count":{c:1 for c in case_names}},
            "timing":{"db_s":elapsed,"parse_s":0,"parser":"case_name"}}


# ─────────────────────────────────────────────────────────────────
#  REPORTS
# ─────────────────────────────────────────────────────────────────
def report_top_contacts(target, days=30, mongo_uri="mongodb://localhost:27017/"):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    col   = get_collection("CDR","CallDetailRecords",mongo_uri)
    results = _agg_safe(col, [
        {"$match": {"A_Party":target,"SDateTime":{"$gte":since}}},
        {"$group": {"_id":"$B_Party","calls":{"$sum":1},
                    "total_dur":{"$sum":"$Duration"},"types":{"$addToSet":"$Call_Type"}}},
        {"$sort": {"calls":-1}},{"$limit":20},
    ], hint="A_Party_1")
    return {"report":"Top Contacts","target":target,"period_days":days,
            "contacts":[{"number":x["_id"],"calls":x["calls"],
                         "duration":secs_human(x["total_dur"]),"types":x["types"]}
                        for x in results if x["_id"]]}

def report_tower_timeline(target, date_str, mongo_uri="mongodb://localhost:27017/"):
    dt    = dateutil_parser.parse(date_str).replace(tzinfo=timezone.utc)
    start = dt.replace(hour=0,minute=0,second=0); end=dt.replace(hour=23,minute=59,second=59)
    col   = get_collection("CDR","CallDetailRecords",mongo_uri)
    raw   = _agg_safe(col, [
        {"$match":   {"A_Party":target,"SDateTime":{"$gte":start,"$lte":end}}},
        {"$sort":    {"SDateTime":1}},{"$limit":MAX_RECORDS},
        {"$project": {"_id":0,"SDateTime":1,"First_CGI":1,"B_Party":1,
                      "Call_Type":1,"Duration":1,"IMEI":1}},
    ], hint="A_Party_1")
    return {"report":"Tower Timeline","target":target,"date":date_str,"events":[{
        "time":    r["SDateTime"].isoformat() if isinstance(r.get("SDateTime"),datetime) else r.get("SDateTime"),
        "tower":   r.get("First_CGI"), "called": r.get("B_Party"),
        "type":    r.get("Call_Type"), "duration": secs_human(int(r.get("Duration",0))),
        "imei":    r.get("IMEI")} for r in raw]}

def report_imei_history(imei, days=30, mongo_uri="mongodb://localhost:27017/"):
    since   = datetime.now(timezone.utc) - timedelta(days=days)
    col     = get_collection("CDR","CallDetailRecords",mongo_uri)
    results = _agg_safe(col, [
        {"$match": {"IMEI":imei,"SDateTime":{"$gte":since}}},
        {"$group": {"_id":"$A_Party","calls":{"$sum":1},
                    "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}},
        {"$sort": {"calls":-1}},{"$limit":50},
    ], hint="IMEI_1")
    return {"report":"IMEI History","imei":imei,"period_days":days,"subscribers":[{
        "number": x["_id"],"calls":x["calls"],
        "first_seen": x["first"].isoformat() if isinstance(x["first"],datetime) else x["first"],
        "last_seen":  x["last"].isoformat()  if isinstance(x["last"], datetime) else x["last"]}
        for x in results if x["_id"]]}

# ─────────────────────────────────────────────────────────────────
#  SPECIAL QUERY DETECTION
# ─────────────────────────────────────────────────────────────────
_DATE_PAT = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?"
    r"|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}\s*,?\s*\d{4}"
    r"|\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+\d{2,4})\b", re.I)

_CASE_SIGNAL_WORDS   = {"case","cases","crime","crimes","belongs","related","linked",
                         "associated","involved","participant","which","what"}
_CASE_LOOKUP_RE      = re.compile(
    r'\b(?:belongs?\s+to\s+(?:which\s+)?case|related\s+to\s+(?:which\s+)?case|'
    r'in\s+which\s+case|which\s+case|case\s+(?:details?|info(?:rmation)?)|'
    r'kis\s+case\s+mein|edhaina\s+case)\b', re.I)
_BPARTY_CASE_RE      = re.compile(r'\b(b\s*party|b-party|bparty|called\s+by|participant)\b', re.I)
_DIST_RE             = re.compile(r'\b(daily|weekly|monthly)\s+(?:summary|distribution|report|breakdown|stats?)\b', re.I)

def _parse_range_dates(dates):
    if len(dates) < 2: raise ValueError(f"Need 2 dates, got: {dates}")
    parsed = []
    for d in dates[:2]:
        is_iso = bool(re.match(r"^\d{4}[/\-]", d.strip()))
        dt = dateutil_parser.parse(d, dayfirst=not is_iso)
        parsed.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
    parsed.sort(); return parsed[0], parsed[1]


def detect_special_query(q: str):
    # Pre-normalise aliases
    q  = _pre_normalise(normalise_query(q))
    ql = q.lower()

    # ── CASE-NAME QUERIES  (must check BEFORE number-based queries) ──
    if _has_case_name(q):
        case_names = _extract_case_names(q)
        if case_names:
            # Determine sub-intent
            has_imei   = "imei"  in ql and "imsi" not in ql
            has_imsi   = "imsi"  in ql
            has_common = any(ph in ql for ph in ("common","common b party","common contact","common number"))
            has_top    = any(ph in ql for ph in ("top contact","top contacts","most called","most contacted","frequent contact"))
            has_caller = any(ph in ql for ph in ("frequent caller","who calls","numbers calling","callers of","calling target"))
            has_first  = any(w in ql for w in ("first","earliest"))
            has_last   = any(w in ql for w in ("last","latest","recent"))
            has_target = any(ph in ql for ph in ("target","targets","a party","numbers in","all numbers"))
            has_tower  = any(w in ql for w in ("tower","towers","cgi","cell","bts"))
            dist_m     = _DIST_RE.search(ql)

            if dist_m:
                return f"case_{dist_m.group(1).lower()}_distribution", {"case_names": case_names}
            if has_imei:
                return "case_imei",              {"case_names": case_names}
            if has_imsi:
                return "case_imsi",              {"case_names": case_names}
            if has_common:
                return "case_common_bparty",     {"case_names": case_names}
            if has_top:
                return "case_top_contacts",      {"case_names": case_names,
                                                  "n": int(m.group(1)) if (m:=re.search(r'\btop\s+(\d+)\b',ql)) else 20}
            if has_caller:
                return "case_frequent_callers",  {"case_names": case_names,
                                                  "n": int(m.group(1)) if (m:=re.search(r'\btop\s+(\d+)\b',ql)) else 20}
            if has_first and has_last:
                return "case_first_last_call",   {"case_names": case_names, "mode":"both"}
            if has_first:
                return "case_first_call",        {"case_names": case_names, "mode":"first"}
            if has_last:
                return "case_last_call",         {"case_names": case_names, "mode":"last"}
            if has_target:
                return "case_targets",           {"case_names": case_names}
            if has_tower:
                return "case_towers",            {"case_names": case_names}
            # Default: show CDR linked to case
            return "case_cdr",                   {"case_names": case_names}

    # ── Distribution queries (number-based) ──────────────────────
    dist_m = _DIST_RE.search(ql)
    if dist_m:
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if not re.match(r"^(19|20)\d{2}$", n)]
        if nums: return f"{dist_m.group(1).lower()}_distribution", {"target": nums[0]}

    # ── Case lookup (which case does a number belong to) ─────────
    has_case = _CASE_LOOKUP_RE.search(ql) is not None
    if not has_case:
        has_case = bool(set(ql.split()) & _CASE_SIGNAL_WORDS) and bool(re.search(r'\b\d{7,15}\b', q))
    if has_case:
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if not re.match(r"^(19|20)\d{2}$", n)]
        if nums:
            return ("case_by_bparty" if _BPARTY_CASE_RE.search(ql) else "case_by_target"), \
                   {"number": nums[0]}

    # ── Frequent callers ─────────────────────────────────────────
    if any(ph in ql for ph in ("calling target","who calls","who called","calls to target",
            "numbers that call","numbers calling","callers of","callers to",
            "kaun karta hai call","call karne wale","call chesevaru","call chesina")):
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if len(n)<15 and not re.match(r"^(19|20)\d{2}$", n)]
        if nums:
            _t = re.search(r'\btop\s+(\d+)\b', ql)
            return "frequent_callers", {"target":nums[0],"n":int(_t.group(1)) if _t else 20}

    # ── Frequent contacted ────────────────────────────────────────
    if any(ph in ql for ph in ("contacted by target","numbers contacted","called by target",
            "target contacts","target called","who does target call","numbers dialled by",
            "numbers dialed by","target ne call kiya","jisko call kiya","target chesina calls")):
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if len(n)<15 and not re.match(r"^(19|20)\d{2}$", n)]
        if nums:
            _t = re.search(r'\btop\s+(\d+)\b', ql)
            return "frequent_contacted", {"target":nums[0],"n":int(_t.group(1)) if _t else 20}

    # ── All IMEI / IMSI ──────────────────────────────────────────
    if "imei" in ql and "imsi" not in ql:
        if any(ph in ql for ph in ("all imei","imei list","imei used","imei of target","imei for target")):
            nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                    if len(n)<15 and not re.match(r"^(19|20)\d{2}$", n)]
            if nums: return "all_imei", {"target":nums[0]}

    if "imsi" in ql:
        if any(ph in ql for ph in ("all imsi","imsi list","imsi used","imsi of target","imsi for target")):
            nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                    if len(n)<15 and not re.match(r"^(19|20)\d{2}$", n)]
            if nums: return "all_imsi", {"target":nums[0]}

    # ── Top contacts ──────────────────────────────────────────────
    if any(ph in ql for ph in ("top contact","top contacts","frequent contact","most called",
            "most contacted","top b party","contact list","contacts of","contacts for",
            "top sampark","adhik sampark","zyada baat","sabse zyada","frequent sampark")):
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if len(n)<15 and not re.match(r"^(19|20)\d{2}$", n)]
        if nums:
            _t = re.search(r'\btop\s+(\d+)\b', ql)
            return "top_contacts", {"target":nums[0],"n":int(_t.group(1)) if _t else 20}

    # ── New / missing numbers ─────────────────────────────────────
    is_new  = any(ph in ql for ph in ("new number","new numbers","new calls","new contacts",
        "naya number","naye number","kotha number","kotta number","pudha number"))
    is_miss = any(ph in ql for ph in ("missing number","missing numbers","gayab number",
        "na mila number","ledu number","gurbuku number","kalipoledu"))
    if is_new or is_miss:
        nums  = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                 if not re.match(r"^(19|20)\d{2}$", n)]
        dates = _DATE_PAT.findall(q)
        return ("new_number" if is_new else "missing_number"), {"targets":nums,"dates":dates}

    # ── Common B party ────────────────────────────────────────────
    if any(ph in ql for ph in ("common","milte jul","sanjha","common b party","common contact")):
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if not re.match(r"^(19|20)\d{2}$", n)]
        if len(nums) >= 2: return "common_b_party", {"targets":nums}

    # ── First + last ──────────────────────────────────────────────
    if (any(w in ql for w in ("first","earliest","pehli","pehla","shuruat")) and
            any(w in ql for w in ("last","latest","recent","aakhri","antim"))):
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if not re.match(r"^(19|20)\d{2}$", n)]
        if nums: return "first_last_call", {"target":nums[0]}

    _FP = re.compile(r'\b(?:first|pehle?)\s+(day|din|week|hafta|month|mahina)\b', re.I)
    fp = _FP.search(ql)
    if fp:
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if not re.match(r"^(19|20)\d{2}$", n)]
        if nums:
            p = {"din":"day","hafta":"week","mahina":"month"}.get(fp.group(1).lower(), fp.group(1).lower())
            return "first_period", {"target":nums[0],"period":p}

    if any(ph in ql for ph in ("first call","earliest call","first record","earliest record",
            "pehli call","pehla call","shuruat ki call","pehla record")):
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if not re.match(r"^(19|20)\d{2}$", n)]
        if nums: return "first_call", {"target":nums[0]}

    if any(ph in ql for ph in ("last call","latest call","most recent call","last record",
            "latest record","recent call","aakhri call","antim call","last contacted",
            "last contact","last dialled","last dialed","last number contacted",
            "recently contacted","recently called","last b party","latest b party",
            "chivari contact","aakhri contact","antim sampark")):
        nums = [n for n in re.findall(r"\b(\d{7,15})\b", q)
                if not re.match(r"^(19|20)\d{2}$", n)]
        if nums: return "last_call", {"target":nums[0]}

    return None, None

# ─────────────────────────────────────────────────────────────────
#  NUMBER-BASED CASE LOOKUP (which case does number X belong to)
# ─────────────────────────────────────────────────────────────────
def query_case_by_target(target, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); db = get_client(mongo_uri)["CDR"]; col = db["CallDetailRecords"]
    seq_rows = _agg_safe(col, [
        {"$match":  {"A_Party": target}},
        {"$unwind": {"path":"$seq_id","preserveNullAndEmptyArrays":False}},
        {"$group":  {"_id":"$seq_id"}},
    ], hint="A_Party_1", limit=100000)
    seq_ids = [r["_id"] for r in seq_rows if r.get("_id")]
    elapsed = round(time.time()-t0, 3)
    if not seq_ids:
        return {"status":"empty","query_type":"case_by_target","target":target,"count":0,
                "records":[],"case_summary":{},"summary":f"No seq_id links for target {target}."}
    nexus = list(db["DataNexus"].find({"_id":{"$in":seq_ids}},{"_id":1,"CrimeID":1}))
    crime_ids = list({str(r["CrimeID"]) for r in nexus if r.get("CrimeID")})
    if not crime_ids:
        return {"status":"empty","query_type":"case_by_target","target":target,"count":0,
                "records":[],"case_summary":{},"summary":f"Target {target}: no DataNexus links."}
    crime_rows = list(db["CrimeRegistry"].find({"_id":{"$in":crime_ids}}))
    elapsed = round(time.time()-t0, 3)
    if not crime_rows:
        return {"status":"empty","query_type":"case_by_target","target":target,"count":0,
                "records":[],"case_summary":{},"summary":f"No CrimeRegistry match for {target}."}
    cases = [{"case":r.get("Crime","-"),"area":r.get("AreaLocation","-")} for r in crime_rows]
    records = [{"_label":f"Case #{i+1}","A_Party":target,"case":c["case"],"area":c["area"]}
               for i,c in enumerate(cases)]
    summary = f"Target {target} linked to {len(cases)} case(s):\n" + \
              "\n".join(f"  • {c['case']}  (area: {c['area']})" for c in cases)
    return {"status":"success","query_type":"case_by_target","target":target,"party_type":"A_Party",
            "count":len(cases),"records":records,"summary":summary,
            "case_summary":{"unique_cases":len(cases),"case_hit_count":{c["case"]:1 for c in cases}},
            "analytics":{"target":target,"case_count":len(cases),
                         "case_names":" | ".join(c["case"] for c in cases),"cases":cases},
            "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}

def query_case_by_bparty(bparty, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); db = get_client(mongo_uri)["CDR"]; col = db["CallDetailRecords"]
    seq_rows = _agg_safe(col, [
        {"$match":  {"B_Party": bparty}},
        {"$unwind": {"path":"$seq_id","preserveNullAndEmptyArrays":False}},
        {"$group":  {"_id":"$seq_id"}},
    ], hint="B_Party_1", limit=100000)
    seq_ids = [r["_id"] for r in seq_rows if r.get("_id")]
    elapsed = round(time.time()-t0, 3)
    if not seq_ids:
        return {"status":"empty","query_type":"case_by_bparty","b_party":bparty,"count":0,
                "records":[],"case_summary":{},"summary":f"No seq_id links for B_Party {bparty}."}
    nexus = list(db["DataNexus"].find({"_id":{"$in":seq_ids}},{"_id":1,"CrimeID":1}))
    crime_ids = list({str(r["CrimeID"]) for r in nexus if r.get("CrimeID")})
    if not crime_ids:
        return {"status":"empty","query_type":"case_by_bparty","b_party":bparty,"count":0,
                "records":[],"case_summary":{},"summary":f"B_Party {bparty}: no DataNexus links."}
    crime_rows = list(db["CrimeRegistry"].find({"_id":{"$in":crime_ids}}))
    elapsed = round(time.time()-t0, 3)
    if not crime_rows:
        return {"status":"empty","query_type":"case_by_bparty","b_party":bparty,"count":0,
                "records":[],"case_summary":{},"summary":f"No CrimeRegistry match for B_Party {bparty}."}
    cases = [{"case":r.get("Crime","-"),"area":r.get("AreaLocation","-")} for r in crime_rows]
    records = [{"_label":f"Case #{i+1}","B_Party":bparty,"case":c["case"],"area":c["area"]}
               for i,c in enumerate(cases)]
    summary = f"B_Party {bparty} linked to {len(cases)} case(s):\n" + \
              "\n".join(f"  • {c['case']}  (area: {c['area']})" for c in cases)
    return {"status":"success","query_type":"case_by_bparty","b_party":bparty,"party_type":"B_Party",
            "count":len(cases),"records":records,"summary":summary,
            "case_summary":{"unique_cases":len(cases),"case_hit_count":{c["case"]:1 for c in cases}},
            "analytics":{"b_party":bparty,"case_count":len(cases),
                         "case_names":" | ".join(c["case"] for c in cases),"cases":cases},
            "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}

# ─────────────────────────────────────────────────────────────────
#  NUMBER-BASED SPECIAL QUERY FUNCTIONS
# ─────────────────────────────────────────────────────────────────
def _bp_all(col, target):
    rows = _agg_safe(col, [
        {"$match":{"A_Party":target}},
        {"$group":{"_id":"$B_Party","total":{"$sum":1},"duration":{"$sum":"$Duration"},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}},
    ], hint="A_Party_1", limit=100000)
    return {r["_id"]: r for r in rows if r["_id"]}

def _bp_range(col, target, s, e):
    rows = _agg_safe(col, [
        {"$match":{"A_Party":target,"SDateTime":{"$gte":s,"$lte":e}}},
        {"$group":{"_id":"$B_Party","total":{"$sum":1},"duration":{"$sum":"$Duration"},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}},
    ], hint="A_Party_1", limit=100000)
    return {r["_id"]: r for r in rows if r["_id"]}

def _build_records(result_map, targets):
    all_nums = set()
    for v in result_map.values(): all_nums.update(v.keys())
    records = []
    for num in all_nums:
        matched = [t for t in targets if num in result_map[t]]
        if not matched: continue
        row = {"Number":num,"Count":len(matched),"Found in":" & ".join(matched)}
        firsts, lasts = [], []
        for t in targets:
            if t in matched:
                d = result_map[t][num]; row[t] = "YES"
                f, l = _utc(d.get("first")), _utc(d.get("last"))
                if f: firsts.append(f)
                if l: lasts.append(l)
            else: row[t] = "-"
        row["First & Last Call"] = f"{_fmt(min(firsts))} - {_fmt(max(lasts))}" if firsts else "-"
        records.append(row)
    records.sort(key=lambda x: (-x["Count"], x["Number"]))
    return records

def query_all_imei(target, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"A_Party":target,"IMEI":{"$exists":True,"$ne":None,"$ne":""}}},
        {"$group": {"_id":"$IMEI","count":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},"first_seen":{"$min":"$SDateTime"},
            "last_seen":{"$max":"$SDateTime"},"imsi_set":{"$addToSet":"$IMSI"},
            "seq_ids":{"$addToSet":"$seq_id"}}},
        {"$sort":{"count":-1}},
    ], hint="A_Party_1")
    if not rows:
        return {"status":"empty","query_type":"all_imei","target":target,"count":0,"records":[],
                "summary":"No IMEI records.","analytics":{},"case_summary":{}}
    records = [
        {"_label":f"IMEI #{i+1}","A_Party":target,"IMEI":r["_id"],
         "IMEI_TAC":r["_id"][:8] if r["_id"] and len(r["_id"])>=8 else r["_id"],
         "count":r["count"],"call_out":r["call_out"],"call_in":r["call_in"],
         "sms_out":r["sms_out"],"sms_in":r["sms_in"],
         "duration":secs_human(int(r.get("duration") or 0)),
         "first_seen":_fmt(r.get("first_seen")),"last_seen":_fmt(r.get("last_seen")),
         "imsi_used":" , ".join([x for x in r.get("imsi_set",[]) if x]),
         "seq_id":[s for sub in(r.get("seq_ids") or []) for s in(sub if isinstance(sub,list) else [sub]) if s]}
        for i, r in enumerate(rows) if r["_id"]
    ]
    summary = "\n".join([f"Target {target} used {len(records)} IMEI(s):"]+
        [f"  [{i+1}] {r['IMEI']}  |  {r['count']} records  |  {r['first_seen']} → {r['last_seen']}"
         for i, r in enumerate(records)])
    result = {"status":"success","query_type":"all_imei","target":target,
              "query":f"{{'A_Party':'{target}'}} → GROUP BY IMEI","hint_used":"A_Party_1","parser":"special",
              "count":len(records),"records":records,"summary":summary,
              "analytics":{"total_imei_count":len(records),"imei_list":" , ".join([r["IMEI"] for r in records]),
                           "most_used_imei":records[0]["IMEI"] if records else None},
              "timing":{"db_s":round(time.time()-t0,3),"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_all_imsi(target, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match": {"A_Party":target,"IMSI":{"$exists":True,"$ne":None,"$ne":""}}},
        {"$group": {"_id":"$IMSI","count":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},"first_seen":{"$min":"$SDateTime"},
            "last_seen":{"$max":"$SDateTime"},"imei_set":{"$addToSet":"$IMEI"},
            "seq_ids":{"$addToSet":"$seq_id"}}},
        {"$sort":{"count":-1}},
    ], hint="A_Party_1")
    if not rows:
        return {"status":"empty","query_type":"all_imsi","target":target,"count":0,"records":[],
                "summary":"No IMSI records.","analytics":{},"case_summary":{}}
    records = [
        {"_label":f"IMSI #{i+1}","A_Party":target,"IMSI":r["_id"],
         "count":r["count"],"call_out":r["call_out"],"call_in":r["call_in"],
         "sms_out":r["sms_out"],"sms_in":r["sms_in"],
         "duration":secs_human(int(r.get("duration") or 0)),
         "first_seen":_fmt(r.get("first_seen")),"last_seen":_fmt(r.get("last_seen")),
         "imei_used":" , ".join([x for x in r.get("imei_set",[]) if x]),
         "seq_id":[s for sub in(r.get("seq_ids") or []) for s in(sub if isinstance(sub,list) else [sub]) if s]}
        for i, r in enumerate(rows) if r["_id"]
    ]
    summary = "\n".join([f"Target {target} used {len(records)} IMSI(s):"]+
        [f"  [{i+1}] {r['IMSI']}  |  {r['count']} records  |  {r['first_seen']} → {r['last_seen']}"
         for i, r in enumerate(records)])
    result = {"status":"success","query_type":"all_imsi","target":target,
              "query":f"{{'A_Party':'{target}'}} → GROUP BY IMSI","hint_used":"A_Party_1","parser":"special",
              "count":len(records),"records":records,"summary":summary,
              "analytics":{"total_imsi_count":len(records),"imsi_list":" , ".join([r["IMSI"] for r in records]),
                           "most_used_imsi":records[0]["IMSI"] if records else None},
              "timing":{"db_s":round(time.time()-t0,3),"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_common_b_party(targets, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    with ThreadPoolExecutor(max_workers=min(len(targets),8)) as ex:
        results = list(ex.map(lambda t: _bp_all(col,t), targets))
    bp_map = dict(zip(targets, results))
    all_nums = set()
    for v in bp_map.values(): all_nums.update(v.keys())
    common_map = {t: {num:bp_map[t][num] for num in all_nums
                      if num in bp_map[t] and sum(1 for tt in targets if num in bp_map[tt])>=2}
                  for t in targets}
    records = _build_records(common_map, targets)
    for r in records: r["Common in CDRs"] = r.pop("Found in")
    result = {"status":"success","query_type":"common_b_party","targets":targets,
              "target_count":len(targets),"per_target_contacts":{t:len(bp_map[t]) for t in targets},
              "common_count":len(records),"common":records,"records":records,
              "timing":{"db_s":round(time.time()-t0,3),"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_new_numbers(targets, start_dt, end_dt, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    def process(target):
        all_bp = _bp_all(col, target); in_rng = set(_bp_range(col,target,start_dt,end_dt).keys())
        return {bp:s for bp,s in all_bp.items() if bp not in in_rng and
                ((_utc(s.get("first")) and _utc(s.get("first"))<start_dt) or
                 (_utc(s.get("last"))  and _utc(s.get("last")) >end_dt))}
    with ThreadPoolExecutor(max_workers=min(len(targets),8)) as ex:
        results = list(ex.map(process, targets))
    result_map = dict(zip(targets, results)); records = _build_records(result_map, targets)
    result = {"status":"success","query_type":"new_numbers","targets":targets,
              "range_start":_fmt(start_dt),"range_end":_fmt(end_dt),
              "description":"B_Party ABSENT in range but PRESENT outside",
              "count":len(records),"new_numbers":records,"records":records,
              "timing":{"db_s":round(time.time()-t0,3),"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_missing_numbers(targets, start_dt, end_dt, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    def process(target):
        all_bp = _bp_all(col, target); in_rng = _bp_range(col,target,start_dt,end_dt); result={}
        for bp,stats in in_rng.items():
            overall = all_bp.get(bp, stats)
            f = _utc(overall.get("first") or stats.get("first"))
            l = _utc(overall.get("last")  or stats.get("last"))
            if (f is None or f>=start_dt) and (l is None or l<=end_dt): result[bp]=stats
        return result
    with ThreadPoolExecutor(max_workers=min(len(targets),8)) as ex:
        results = list(ex.map(process, targets))
    result_map = dict(zip(targets, results)); records = _build_records(result_map, targets)
    result = {"status":"success","query_type":"missing_numbers","targets":targets,
              "range_start":_fmt(start_dt),"range_end":_fmt(end_dt),
              "description":"B_Party PRESENT in range but ABSENT outside",
              "count":len(records),"missing_numbers":records,"records":records,
              "timing":{"db_s":round(time.time()-t0,3),"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_first_last_call(target, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri); p = _proj()
    r = _agg_safe(col, [{"$match":{"A_Party":target}},{"$facet":{
        "first_call":[{"$sort":{"SDateTime":1}},{"$limit":1},{"$project":p}],
        "last_call": [{"$sort":{"SDateTime":-1}},{"$limit":1},{"$project":p}],
        "stats":     [{"$group":{"_id":None,"total":{"$sum":1},
                                 "first":{"$min":"$SDateTime"},"last":{"$max":"$SDateTime"}}}],
    }}], hint="A_Party_1", limit=1)
    elapsed = round(time.time()-t0, 3)
    if not r: return {"status":"empty","query_type":"first_last_call","target":target,"count":0,
                      "records":[],"summary":"No records.","analytics":{},"case_summary":{}}
    r = r[0]; fd = serialize_doc(r["first_call"][0]) if r["first_call"] else None
    ld = serialize_doc(r["last_call"][0]) if r["last_call"] else None
    stats = r["stats"][0] if r["stats"] else {}
    fd_dt = _utc(stats.get("first")); ld_dt = _utc(stats.get("last"))
    span = (ld_dt-fd_dt).days if fd_dt and ld_dt else None
    records = []
    if fd: fd["_label"]="FIRST CALL"; records.append(fd)
    if ld and (not fd or ld.get("_id")!=fd.get("_id")): ld["_label"]="LAST CALL"; records.append(ld)
    result = {"status":"success" if records else "empty","query_type":"first_last_call",
              "target":target,"query":f"{{'A_Party':'{target}'}}","hint_used":"A_Party_1","parser":"special",
              "count":len(records),"records":records,
              "summary":(f"First: {_fmt(fd_dt)}\nLast: {_fmt(ld_dt)}\n"
                         f"Total: {stats.get('total',0)}\nSpan: {span} days") if records else "No records.",
              "analytics":{"total_records":int(stats.get("total",0)),"first_call_datetime":_fmt(fd_dt),
                           "last_call_datetime":_fmt(ld_dt),"active_span_days":span},
              "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_last_call(target, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri); p = _proj()
    r = _agg_safe(col, [{"$match":{"A_Party":target}},{"$sort":{"SDateTime":-1}},
                         {"$limit":1},{"$project":p}], hint="A_Party_1", limit=1)
    elapsed = round(time.time()-t0, 3)
    if not r: return {"status":"empty","query_type":"last_call","target":target,"count":0,
                      "records":[],"summary":"No records.","analytics":{},"case_summary":{}}
    doc = serialize_doc(r[0]); doc["_label"] = "LAST CALL"
    result = {"status":"success","query_type":"last_call","target":target,
              "query":f"{{'A_Party':'{target}'}}","hint_used":"A_Party_1","parser":"special",
              "count":1,"records":[doc],
              "summary":(f"Last call: {doc.get('SDateTime','-')}\nB Party: {doc.get('B_Party','-')}\n"
                         f"Type: {doc.get('Call_Type','-')}\n"
                         f"Duration: {secs_human(int(doc.get('Duration') or 0))}\nTower: {doc.get('First_CGI','-')}"),
              "analytics":{"last_call_datetime":doc.get("SDateTime","-"),"b_party":doc.get("B_Party","-"),
                           "call_type":doc.get("Call_Type","-"),
                           "duration":secs_human(int(doc.get("Duration") or 0)),"tower":doc.get("First_CGI","-")},
              "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_first_call(target, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri); p = _proj()
    r = _agg_safe(col, [{"$match":{"A_Party":target}},{"$sort":{"SDateTime":1}},
                         {"$limit":1},{"$project":p}], hint="A_Party_1", limit=1)
    elapsed = round(time.time()-t0, 3)
    if not r: return {"status":"empty","query_type":"first_call","target":target,"count":0,
                      "records":[],"summary":"No records.","analytics":{},"case_summary":{}}
    doc = serialize_doc(r[0]); doc["_label"] = "FIRST CALL"
    result = {"status":"success","query_type":"first_call","target":target,
              "query":f"{{'A_Party':'{target}'}}","hint_used":"A_Party_1","parser":"special",
              "count":1,"records":[doc],
              "summary":(f"First call: {doc.get('SDateTime','-')}\nB Party: {doc.get('B_Party','-')}\n"
                         f"Type: {doc.get('Call_Type','-')}\n"
                         f"Duration: {secs_human(int(doc.get('Duration') or 0))}\nTower: {doc.get('First_CGI','-')}"),
              "analytics":{"first_call_datetime":doc.get("SDateTime","-"),"b_party":doc.get("B_Party","-"),
                           "call_type":doc.get("Call_Type","-"),
                           "duration":secs_human(int(doc.get("Duration") or 0)),"tower":doc.get("First_CGI","-")},
              "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_first_period(target, period, mongo_uri="mongodb://localhost:27017/", limit=MAX_RECORDS):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri); p = _proj()
    fr = _agg_safe(col, [{"$match":{"A_Party":target}},{"$sort":{"SDateTime":1}},
                          {"$limit":1},{"$project":{"SDateTime":1,"_id":0}}],
                   hint="A_Party_1", limit=1)
    if not fr:
        return {"status":"empty","query_type":f"first_{period}","target":target,"count":0,
                "records":[],"summary":"No records.","analytics":{},"case_summary":{}}
    fd = _utc(fr[0]["SDateTime"]); ed = fd + timedelta(days={"day":1,"week":7,"month":30}[period])
    raw = _agg_safe(col, [{"$match":{"A_Party":target,"SDateTime":{"$gte":fd,"$lt":ed}}},
                           {"$sort":{"SDateTime":1}},{"$limit":limit},{"$project":p}],
                    hint="A_Party_1", limit=limit)
    records = [serialize_doc(r) for r in raw]
    pl = {"day":"24 hours","week":"7 days","month":"30 days"}[period]
    result = {"status":"success" if records else "empty","query_type":f"first_{period}",
              "target":target,"hint_used":"A_Party_1","parser":"special",
              "count":len(records),"records":records,
              "summary":f"First {period}: {_fmt(fd)} → {_fmt(ed)}\nRecords in first {pl}: {len(records)}",
              "analytics":{"period":f"first {pl}","window_start":_fmt(fd),
                           "window_end":_fmt(ed),"record_count":len(records)},
              "timing":{"db_s":round(time.time()-t0,3),"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_top_contacts(target, n=20, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match":{"A_Party":target}},
        {"$group":{"_id":"$B_Party","total":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},"first_seen":{"$min":"$SDateTime"},
            "last_seen":{"$max":"$SDateTime"},"seq_ids":{"$addToSet":"$seq_id"}}},
        {"$sort":{"total":-1}},{"$limit":n},
    ], hint="A_Party_1")
    elapsed = round(time.time()-t0, 3)
    if not rows: return {"status":"empty","query_type":"top_contacts","target":target,"n":n,"count":0,
                         "records":[],"summary":f"No contacts for {target}.","analytics":{},"case_summary":{}}
    records = [
        {"_label":f"#{rank}","rank":rank,"B_Party":r["_id"],"total":r["total"],
         "call_out":r["call_out"],"call_in":r["call_in"],"sms_out":r["sms_out"],"sms_in":r["sms_in"],
         "duration":secs_human(int(r.get("duration") or 0)),
         "first_seen":_fmt(r.get("first_seen")),"last_seen":_fmt(r.get("last_seen")),
         "seq_id":[s for sub in(r.get("seq_ids") or []) for s in(sub if isinstance(sub,list) else [sub]) if s]}
        for rank, r in enumerate(rows, 1) if r["_id"]
    ]
    total_i = sum(r["total"] for r in records)
    summary = "\n".join([f"Top {len(records)} contacts of {target}:",f"  Total: {total_i}",""]+
        [f"  #{r['rank']:>2}  {r['B_Party']:<15}  {r['total']:>5}  "
         f"(cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']})  "
         f"{r['first_seen']} → {r['last_seen']}" for r in records])
    result = {"status":"success","query_type":"top_contacts","target":target,"n":n,
              "query":f"{{'A_Party':'{target}'}} → GROUP BY B_Party SORT total DESC LIMIT {n}",
              "hint_used":"A_Party_1","parser":"special","count":len(records),"records":records,
              "summary":summary,"analytics":{"target":target,"top_n":n,"contact_count":len(records),
                                             "total_interactions":total_i,"contacts":records},
              "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_frequent_callers(target, n=20, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match":{"B_Party":target}},
        {"$group":{"_id":"$A_Party","total":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},"first_seen":{"$min":"$SDateTime"},
            "last_seen":{"$max":"$SDateTime"},"seq_ids":{"$addToSet":"$seq_id"}}},
        {"$sort":{"total":-1}},{"$limit":n},
    ], hint="B_Party_1")
    elapsed = round(time.time()-t0, 3)
    if not rows: return {"status":"empty","query_type":"frequent_callers","target":target,"n":n,"count":0,
                         "records":[],"summary":f"No callers for {target}.","analytics":{},"case_summary":{}}
    records = [
        {"_label":f"#{rank}","rank":rank,"A_Party":r["_id"],"B_Party":target,"total":r["total"],
         "call_out":r["call_out"],"call_in":r["call_in"],"sms_out":r["sms_out"],"sms_in":r["sms_in"],
         "duration":secs_human(int(r.get("duration") or 0)),
         "first_seen":_fmt(r.get("first_seen")),"last_seen":_fmt(r.get("last_seen")),
         "seq_id":[s for sub in(r.get("seq_ids") or []) for s in(sub if isinstance(sub,list) else [sub]) if s]}
        for rank, r in enumerate(rows, 1) if r["_id"]
    ]
    total_i = sum(r["total"] for r in records)
    result = {"status":"success","query_type":"frequent_callers","target":target,"n":n,
              "query":f"{{'B_Party':'{target}'}} → GROUP BY A_Party SORT total DESC LIMIT {n}",
              "hint_used":"B_Party_1","parser":"special","count":len(records),"records":records,
              "summary":"\n".join([f"Top {len(records)} numbers calling {target}:",f"  Total: {total_i}",""]+
                  [f"  #{r['rank']:>2}  {r['A_Party']:<15}  {r['total']:>5}  "
                   f"cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']}  "
                   f"{r['first_seen']} → {r['last_seen']}" for r in records]),
              "analytics":{"target":target,"top_n":n,"caller_count":len(records),
                           "total_interactions":total_i,"callers":records},
              "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

def query_frequent_contacted(target, n=20, mongo_uri="mongodb://localhost:27017/"):
    t0 = time.time(); col = get_collection("CDR","CallDetailRecords",mongo_uri)
    rows = _agg_safe(col, [
        {"$match":{"A_Party":target}},
        {"$group":{"_id":"$B_Party","total":{"$sum":1},
            "call_out":{"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_OUT"]},1,0]}},
            "call_in": {"$sum":{"$cond":[{"$eq":["$Call_Type","CALL_IN"]}, 1,0]}},
            "sms_out": {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_OUT"]}, 1,0]}},
            "sms_in":  {"$sum":{"$cond":[{"$eq":["$Call_Type","SMS_IN"]},  1,0]}},
            "duration":{"$sum":"$Duration"},"first_seen":{"$min":"$SDateTime"},
            "last_seen":{"$max":"$SDateTime"},"seq_ids":{"$addToSet":"$seq_id"}}},
        {"$sort":{"total":-1}},{"$limit":n},
    ], hint="A_Party_1")
    elapsed = round(time.time()-t0, 3)
    if not rows: return {"status":"empty","query_type":"frequent_contacted","target":target,"n":n,"count":0,
                         "records":[],"summary":f"No contacts for {target}.","analytics":{},"case_summary":{}}
    records = [
        {"_label":f"#{rank}","rank":rank,"A_Party":target,"B_Party":r["_id"],"total":r["total"],
         "call_out":r["call_out"],"call_in":r["call_in"],"sms_out":r["sms_out"],"sms_in":r["sms_in"],
         "duration":secs_human(int(r.get("duration") or 0)),
         "first_seen":_fmt(r.get("first_seen")),"last_seen":_fmt(r.get("last_seen")),
         "seq_id":[s for sub in(r.get("seq_ids") or []) for s in(sub if isinstance(sub,list) else [sub]) if s]}
        for rank, r in enumerate(rows, 1) if r["_id"]
    ]
    total_i = sum(r["total"] for r in records)
    result = {"status":"success","query_type":"frequent_contacted","target":target,"n":n,
              "query":f"{{'A_Party':'{target}'}} → GROUP BY B_Party SORT total DESC LIMIT {n}",
              "hint_used":"A_Party_1","parser":"special","count":len(records),"records":records,
              "summary":"\n".join([f"Top {len(records)} numbers contacted by {target}:",f"  Total: {total_i}",""]+
                  [f"  #{r['rank']:>2}  {r['B_Party']:<15}  {r['total']:>5}  "
                   f"cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']}  "
                   f"{r['first_seen']} → {r['last_seen']}" for r in records]),
              "analytics":{"target":target,"top_n":n,"contacted_count":len(records),
                           "total_interactions":total_i,"contacted":records},
              "timing":{"db_s":elapsed,"parse_s":0.0,"parser":"special"}}
    return _enrich_and_summarise(result, mongo_uri)

# ─────────────────────────────────────────────────────────────────
#  DISPATCHER
# ─────────────────────────────────────────────────────────────────
def analyze(user_query, mongo_uri="mongodb://localhost:27017/", raw_mode=False, limit=MAX_RECORDS):
    qtype, params = detect_special_query(user_query)

    # ── Case-name queries ────────────────────────────────────────
    if qtype == "case_cdr":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_cdr(params["case_names"], limit=limit, mongo_uri=mongo_uri, raw_mode=raw_mode)

    if qtype == "case_imei":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_imei(params["case_names"], mongo_uri)

    if qtype == "case_imsi":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_imsi(params["case_names"], mongo_uri)

    if qtype == "case_common_bparty":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_common_bparty(params["case_names"], mongo_uri)

    if qtype == "case_top_contacts":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_top_contacts(params["case_names"], params.get("n",20), mongo_uri)

    if qtype == "case_frequent_callers":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_frequent_callers(params["case_names"], params.get("n",20), mongo_uri)

    if qtype in ("case_first_last_call","case_first_call","case_last_call"):
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_first_last(params["case_names"], params.get("mode","both"), mongo_uri)

    if qtype == "case_targets":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_targets(params["case_names"], mongo_uri)

    if qtype == "case_towers":
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_towers(params["case_names"], mongo_uri)

    if qtype in ("case_daily_distribution","case_weekly_distribution","case_monthly_distribution"):
        dist_type = qtype.replace("case_","").replace("_distribution","")
        print(f"[Dispatcher] {qtype} | {params['case_names']}")
        return query_case_distribution(params["case_names"], dist_type, mongo_uri)

    # ── Number-based case lookup ─────────────────────────────────
    if qtype == "case_by_target":
        print(f"[Dispatcher] {qtype} | {params['number']}")
        return query_case_by_target(params["number"], mongo_uri)

    if qtype == "case_by_bparty":
        print(f"[Dispatcher] {qtype} | {params['number']}")
        return query_case_by_bparty(params["number"], mongo_uri)

    # ── Distribution (number-based) ──────────────────────────────
    if qtype in ("daily_distribution","weekly_distribution","monthly_distribution"):
        dist_type = qtype.replace("_distribution","")
        print(f"[Dispatcher] {qtype} | target={params['target']}")
        return query_distribution(params["target"], dist_type, mongo_uri)

    # ── Standard special queries ─────────────────────────────────
    dispatch = {
        "frequent_callers":   lambda: query_frequent_callers(params["target"],   params["n"],      mongo_uri),
        "frequent_contacted": lambda: query_frequent_contacted(params["target"], params["n"],      mongo_uri),
        "all_imei":           lambda: query_all_imei(params["target"],                              mongo_uri),
        "all_imsi":           lambda: query_all_imsi(params["target"],                              mongo_uri),
        "top_contacts":       lambda: query_top_contacts(params["target"],       params["n"],      mongo_uri),
        "first_last_call":    lambda: query_first_last_call(params["target"],                       mongo_uri),
        "first_call":         lambda: query_first_call(params["target"],                            mongo_uri),
        "last_call":          lambda: query_last_call(params["target"],                             mongo_uri),
        "first_period":       lambda: query_first_period(params["target"],       params["period"], mongo_uri),
        "common_b_party":     lambda: query_common_b_party(params["targets"],                       mongo_uri),
    }
    if qtype in dispatch:
        print(f"[Dispatcher] SPECIAL={qtype} | {params}")
        return dispatch[qtype]()

    if qtype in ("new_number","missing_number"):
        print(f"[Dispatcher] SPECIAL={qtype} | {params}")
        targets = params.get("targets",[])
        if not targets: return {"status":"error","message":"No target numbers found."}
        try: start_dt, end_dt = _parse_range_dates(params.get("dates",[]))
        except ValueError as e:
            return {"status":"error","message":f"Date parse error: {e}. Use 2 dates."}
        fn = query_new_numbers if qtype=="new_number" else query_missing_numbers
        return fn(targets, start_dt, end_dt, mongo_uri)

    print(f"[Dispatcher] CDR | {user_query}")
    return run_cdr_query(user_query, limit=limit, mongo_uri=mongo_uri, raw_mode=raw_mode)

# ─────────────────────────────────────────────────────────────────
#  PRINT RESULT
# ─────────────────────────────────────────────────────────────────
def print_result(result):
    status = result.get("status")
    t      = result.get("timing", {})
    ep     = f" | enrich {t['enrich_s']}s" if 'enrich_s' in t else ""
    t_str  = f"  [DB {t.get('db_s','?')}s | parse {t.get('parse_s','?')}s{ep}]"

    if status == "error":  print(f"\n⚠ ERROR: {result['message']}"); return
    if status == "empty":  print(f"\nNo records. {result.get('summary','')}"); return

    qtype = result.get("query_type",""); a = result.get("analytics",{})
    cn    = result.get("case_names","")
    cn_str= f"  case(s): {', '.join(cn)}" if cn else ""

    # ── Case-name CDR result ──────────────────────────────────────
    if qtype == "case_cdr":
        print(f"\n{'='*62}\n  CDR RECORDS{cn_str}  |  {result['count']} records{t_str}")
        cs = result.get("case_summary",{}); print(f"{'='*62}")
        if cs.get("unique_cases",0)>0:
            for cname,cnt in sorted(cs.get("case_hit_count",{}).items(),key=lambda x:-x[1]):
                print(f"    • {cname}  ({cnt} records)")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Case IMEI / IMSI ─────────────────────────────────────────
    if qtype in ("case_imei","case_imsi"):
        key = "IMEI" if qtype=="case_imei" else "IMSI"
        print(f"\n{'='*62}\n  {key} LIST{cn_str}  |  {result['count']} distinct{t_str}\n{'='*62}")
        for r in result.get("records",[]):
            print(f"  {r[key]}  |  {r['count']} recs  |  {r['first_seen']} → {r['last_seen']}")
            if r.get("imsi_used" if key=="IMEI" else "imei_used"):
                print(f"    {'IMSI' if key=='IMEI' else 'IMEI'}(s): {', '.join(r.get('imsi_used', r.get('imei_used',[])))}")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Case common B party ───────────────────────────────────────
    if qtype == "case_common_bparty":
        print(f"\n{'='*62}\n  COMMON B-PARTY{cn_str}  |  {result['count']} numbers\n{'='*62}")
        for r in result.get("records",[]): print(f"  {r['Number']:<15}  count={r['Count']}  {r.get('Cases','')}")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Case top contacts / frequent callers ──────────────────────
    if qtype in ("case_top_contacts","case_frequent_callers"):
        party = "B_Party" if qtype=="case_top_contacts" else "A_Party"
        print(f"\n{'='*62}\n  {qtype.upper()}{cn_str}  |  {result['count']} records\n{'='*62}")
        for r in result.get("records",[]):
            print(f"  {r['_label']:>4}  {r[party]:<15}  {r['total']:>5}  "
                  f"cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']}  "
                  f"{r['first_seen']} → {r['last_seen']}")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Case first/last ───────────────────────────────────────────
    if qtype == "case_first_last_call":
        print(f"\n{'='*62}\n  FIRST/LAST CALL{cn_str}\n{'='*62}")
        for r in result.get("records",[]):
            print(f"\n  [{r.get('_label','?')}]  {r.get('SDateTime','-')}  "
                  f"A={r.get('A_Party','-')}  B={r.get('B_Party','-')}  "
                  f"{r.get('Call_Type','-')}  {secs_human(int(r.get('Duration') or 0))}")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Case targets / towers ─────────────────────────────────────
    if qtype in ("case_targets","case_towers"):
        key = "A_Party" if qtype=="case_targets" else "First_CGI"
        label = "TARGETS" if qtype=="case_targets" else "TOWERS"
        print(f"\n{'='*62}\n  {label}{cn_str}  |  {result['count']}\n{'='*62}")
        for r in result.get("records",[]): print(f"  {r.get(key,'-'):<20}  records={r.get('total_records',r.get('count',0))}  {r.get('first_seen','-')} → {r.get('last_seen','-')}")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Case / number distribution ────────────────────────────────
    if "distribution" in qtype:
        label = qtype.replace("case_","").replace("_"," ").title()
        subject = cn_str or f"  target: {result.get('target','?')}"
        print(f"\n{'='*62}\n  {label}{subject}  |  {result['count']} periods\n{'='*62}")
        for r in result.get("records",[]):
            print(f"  {r['_label']:>12}  calls={r['total_calls']:>5}  "
                  f"cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']}  "
                  f"dur={r['duration']}")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Number-based case lookup ──────────────────────────────────
    if qtype in ("case_by_target","case_by_bparty"):
        key = "target" if qtype=="case_by_target" else "b_party"
        print(f"\n{'='*62}\n  CASE LOOKUP | {key}={result.get(key,'?')} | cases={result['count']}\n{'='*62}")
        for c in a.get("cases",[]):
            print(f"  Case : {c['case']}\n  Area : {c['area']}\n")
        print(f"\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n"); return

    # ── Standard CDR results ──────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  {result['count']} records | index={result.get('hint_used','?')}{t_str}")
    print(f"  Query: {result.get('query','')}\n{'='*62}")
    cs = result.get("case_summary",{})
    if cs.get("unique_cases",0)>0:
        print(f"\n  CASES: {cs['unique_cases']} unique:")
        for cn2,cnt in sorted(cs.get("case_hit_count",{}).items(),key=lambda x:-x[1]):
            print(f"    • {cn2}  ({cnt} records)")

    if qtype in ("frequent_callers","frequent_contacted","top_contacts"):
        party = "A_Party" if qtype=="frequent_callers" else "B_Party"
        for r in result.get("records",[]):
            print(f"  {r['_label']:>4}  {r[party]:<15}  {r['total']:>5}  "
                  f"cout={r['call_out']} cin={r['call_in']} sout={r['sms_out']} sin={r['sms_in']}  "
                  f"{r['first_seen']} → {r['last_seen']}  cases=[{r.get('case_names','-')}]")
    elif qtype in ("all_imei","all_imsi"):
        key = "IMEI" if qtype=="all_imei" else "IMSI"
        for r in result.get("records",[]):
            print(f"  {r[key]}  |  {r['count']} recs  |  {r['first_seen']} → {r['last_seen']}  cases=[{r.get('case_names','-')}]")
    elif "top_contacts" in a:
        print("\nTOP CONTACTS:")
        for c, n in a["top_contacts"][:5]: print(f"  {c}: {n}")
        print(f"\nDuration total={a.get('total_duration_human')} avg={a.get('avg_duration_seconds')}s max={a.get('max_duration_human')}")
        print(f"Types: {a.get('call_type_breakdown')}")
        if a.get("weekly_distribution"):
            print("\nWEEKLY (top 5):")
            for wk, wd in list(a["weekly_distribution"].items())[:5]:
                print(f"  {wk}  calls={wd.get('calls',wd.get('count',0))} dur={wd.get('duration','-')}")
        if a.get("monthly_distribution"):
            print("\nMONTHLY:")
            for mo, md in a["monthly_distribution"].items():
                print(f"  {mo}  calls={md.get('calls',md.get('count',0))} dur={md.get('duration','-')}")

    print(f"\n{'─'*62}\nSummary:\n{result.get('summary','N/A')}\n{'='*62}\n")

# ─────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────
def interactive_cli(mongo_uri="mongodb://localhost:27017/", case_file=CASE_FILE_PATH):
    init_case_db(case_file)
    print(f"\n{'='*62}")
    print(f"  CDR Analyzer  —  Full Edition with Case-Name Queries")
    print(f"  Model: {MODEL}  |  Max records: {MAX_RECORDS}")
    print(f"{'='*62}")
    print("    CASE-NAME QUERIES (examples):")
    print("    show CDR linked to case342")
    print("    all records of case11 and case22")
    print("    find all imeis used in case11")
    print("    find all imsis used in case11")
    print("    common b party numbers in case11 and case234")
    print("    top contacts of case11")
    print("    frequent callers of case11")
    print("    first and last call of case11")
    print("    weekly summary of case11")
    print("    daily distribution of case11")
    print("    monthly summary of case11")
    print("    targets in case11")
    print("    towers used in case11")
    print("    NUMBER QUERIES: all existing queries unchanged")
    print(f"{'='*62}")
    print("  report:contacts <num> [days]  |  report:towers <num> <date>")
    print("  report:imei <imei> [days]     |  reload:cases [path]  |  exit")
    print(f"{'='*62}\n")
    while True:
        try:   inp = input(">> ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not inp: continue
        lo = inp.lower()
        if lo in ("exit","quit","q"): break
        elif lo.startswith("report:contacts"):
            p=inp.split(); t=p[1] if len(p)>1 else ""; d=int(p[2]) if len(p)>2 else 30
            print(json.dumps(report_top_contacts(t,d,mongo_uri),indent=2,default=str))
        elif lo.startswith("report:towers"):
            p=inp.split(); t=p[1] if len(p)>1 else ""; ds=p[2] if len(p)>2 else ""
            print(json.dumps(report_tower_timeline(t,ds,mongo_uri),indent=2,default=str))
        elif lo.startswith("report:imei"):
            p=inp.split(); t=p[1] if len(p)>1 else ""; d=int(p[2]) if len(p)>2 else 30
            print(json.dumps(report_imei_history(t,d,mongo_uri),indent=2,default=str))
        elif lo.startswith("reload:cases"):
            p=inp.split(); path=p[1] if len(p)>1 else case_file
            init_case_db(path); print(f"[CaseDB] Reloaded {len(_case_db.cases)} cases from {path}")
        else:
            print_result(analyze(inp, mongo_uri=mongo_uri))

if __name__ == "__main__":
    MONGO_URI = "mongodb://localhost:27017/"
    CASE_FILE = os.environ.get("CDR_CASE_FILE","cases.json")
    if len(sys.argv) > 1:
        init_case_db(CASE_FILE)
        print_result(analyze(" ".join(sys.argv[1:]), mongo_uri=MONGO_URI))
    else:
        interactive_cli(MONGO_URI, CASE_FILE)