# import requests
# import re
# import json
# from datetime import datetime
# from pymongo import MongoClient
#
# # ─── CONFIG ───────────────────────────────────────────────────────────────────
# OLLAMA_URL = "http://localhost:11434/api/generate"
# OLLAMA_MODEL_CHAT = "mistral"       # Used for general chat & result summarization
# OLLAMA_MODEL_PIPELINE = "mistral"  # Swap to "phi3:mini" or "llama3.2:3b" for faster pipeline builds
#
# # MongoDB Connection
# try:
#     client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000)
#     db = client["CDR"]
#     cdr_collection = db["CallDetailRecords"]
#     client.server_info()
#     print("✅ Connected to MongoDB successfully.")
# except Exception as e:
#     print(f"❌ CRITICAL: Could not connect to MongoDB: {e}")
#     exit()
#
#
# # ─── LLM UTILITIES ────────────────────────────────────────────────────────────
# def ask_llm(prompt: str, model: str = OLLAMA_MODEL_CHAT) -> str | None:
#     """Sends a prompt to the local Ollama instance."""
#     try:
#         response = requests.post(
#             OLLAMA_URL,
#             json={
#                 "model": model,
#                 "prompt": prompt,
#                 "stream": False,
#                 "options": {"temperature": 0.1}
#             },
#             timeout=120,
#         )
#         return response.json().get("response", "").strip()
#     except Exception as e:
#         print(f"⚠️ LLM Error: {e}")
#         return None
#
#
# def ask_llm_stream(prompt: str, model: str = OLLAMA_MODEL_CHAT) -> str:
#     """
#     Streams LLM tokens directly to stdout so the user sees output immediately
#     instead of waiting for the full response. Returns the complete text.
#     """
#     try:
#         response = requests.post(
#             OLLAMA_URL,
#             json={
#                 "model": model,
#                 "prompt": prompt,
#                 "stream": True,
#                 "options": {"temperature": 0.1}
#             },
#             stream=True,
#             timeout=120,
#         )
#         full_text = ""
#         print("\nAssistant: ", end="", flush=True)
#         for line in response.iter_lines():
#             if line:
#                 chunk = json.loads(line).get("response", "")
#                 print(chunk, end="", flush=True)
#                 full_text += chunk
#         print()  # newline after streaming completes
#         return full_text.strip()
#     except Exception as e:
#         print(f"\n⚠️ LLM Stream Error: {e}")
#         return "Sorry, I encountered an error generating a response."
#
#
# # ─── INTENT DETECTION ─────────────────────────────────────────────────────────
# def detect_intent(user_query: str) -> str:
#     cdr_keywords = [
#         "call", "calls", "contact", "contacts", "called", "duration", "imei",
#         "imsi", "party", "number", "phone", "sms", "cdr", "record", "tower",
#         "location", "top", "most", "frequent", "between", "connected", "dialed",
#         "received", "outgoing", "incoming", "minutes", "seconds", "from", "to",
#         "voice", "sdate", "edate", "a_party", "b_party", "lat", "long"
#     ]
#     q = user_query.lower()
#     return "cdr_query" if any(kw in q for kw in cdr_keywords) else "general_chat"
#
#
# # ─── GENERAL CHAT ─────────────────────────────────────────────────────────────
# def general_chat(user_query: str, history: list) -> str:
#     history_text = "".join(
#         [f"{m['role'].capitalize()}: {m['content']}\n" for m in history[-6:]]
#     )
#     prompt = f"""You are a helpful AI assistant for a telecom CDR analytics platform.
# Be concise and professional.
# Conversation so far:
# {history_text}
# User: {user_query}
# Assistant:"""
#     return ask_llm_stream(prompt)
#
#
# # ─── HARDCODED RULES (No LLM — instant response) ──────────────────────────────
# def force_rules(user_query: str):
#     """
#     Regex-based pipeline builder using the ACTUAL schema:
#       A_Party     — caller phone number (string)
#       B_Party     — called number or service name (string)
#       SDateTime   — call start (ISODate)
#       EDateTime   — call end (ISODate)
#       SDate       — date only (ISODate)
#       STime       — time string HH:MM:SS
#       Duration    — seconds (number)
#       Call_Type   — e.g. SMS_IN, SMS_OUT, VOICE_IN, VOICE_OUT
#       FileServiceType — SMS or VOICE
#       FileCallType    — SMT / VOI / DAT
#       IMEI, IMSI  — device identifiers (string)
#       First_Lat, First_Long — tower GPS coordinates (number)
#       First_CGI   — tower cell ID (string)
#       Con_Type    — Pre / Post (prepaid / postpaid)
#     """
#     q = user_query.lower()
#
#     # Extract phone numbers (7–15 digits)
#     numbers = re.findall(r"\b\d{7,15}\b", user_query)
#
#     # Extract dates in YYYY-MM-DD format
#     dates = re.findall(r"\d{4}-\d{2}-\d{2}", user_query)
#
#     # Extract limit from "top N"
#     limit_match = re.search(r"\btop\s+(\d+)\b", q)
#     limit = int(limit_match.group(1)) if limit_match else 10
#
#     # ── RULE 1: Date range + phone number ──────────────────────────────────────
#     if len(dates) >= 2 and len(numbers) >= 1:
#         try:
#             start_dt = datetime.strptime(dates[0], "%Y-%m-%d")
#             end_dt = datetime.strptime(dates[1], "%Y-%m-%d")
#             return [
#                 {
#                     "$match": {
#                         "A_Party": numbers[0],
#                         "SDateTime": {"$gte": start_dt, "$lte": end_dt}
#                     }
#                 },
#                 {"$sort": {"SDateTime": -1}},
#                 {"$limit": 50}
#             ]
#         except ValueError:
#             pass
#
#     # ── RULE 2: Date range only (no number) ────────────────────────────────────
#     if len(dates) >= 2 and len(numbers) == 0:
#         try:
#             start_dt = datetime.strptime(dates[0], "%Y-%m-%d")
#             end_dt = datetime.strptime(dates[1], "%Y-%m-%d")
#             return [
#                 {
#                     "$match": {
#                         "SDateTime": {"$gte": start_dt, "$lte": end_dt}
#                     }
#                 },
#                 {"$sort": {"SDateTime": -1}},
#                 {"$limit": 50}
#             ]
#         except ValueError:
#             pass
#
#     # ── RULE 3: Top N contacts for a number ────────────────────────────────────
#     if "top" in q and len(numbers) == 1:
#         return [
#             {"$match": {"A_Party": numbers[0]}},
#             {"$group": {"_id": "$B_Party", "total_calls": {"$sum": 1},
#                         "total_duration": {"$sum": "$Duration"}}},
#             {"$sort": {"total_calls": -1}},
#             {"$limit": limit}
#         ]
#
#     # ── RULE 4: SMS records for a number ───────────────────────────────────────
#     if "sms" in q and len(numbers) == 1:
#         return [
#             {"$match": {"A_Party": numbers[0], "FileServiceType": "SMS"}},
#             {"$sort": {"SDateTime": -1}},
#             {"$limit": 50}
#         ]
#
#     # ── RULE 5: Voice/call records for a number ────────────────────────────────
#     if any(kw in q for kw in ["voice", "call", "incoming", "outgoing"]) and len(numbers) == 1:
#         match_filter = {"A_Party": numbers[0]}
#         if "incoming" in q:
#             match_filter["Call_Type"] = {"$regex": "_IN$"}
#         elif "outgoing" in q:
#             match_filter["Call_Type"] = {"$regex": "_OUT$"}
#         return [
#             {"$match": match_filter},
#             {"$sort": {"SDateTime": -1}},
#             {"$limit": 50}
#         ]
#
#     # ── RULE 6: Calls between two numbers ──────────────────────────────────────
#     if "between" in q and len(numbers) == 2:
#         return [
#             {
#                 "$match": {
#                     "$or": [
#                         {"A_Party": numbers[0], "B_Party": numbers[1]},
#                         {"A_Party": numbers[1], "B_Party": numbers[0]}
#                     ]
#                 }
#             },
#             {"$sort": {"SDateTime": -1}},
#             {"$limit": 50}
#         ]
#
#     # ── RULE 7: Location / tower for a number ──────────────────────────────────
#     if any(kw in q for kw in ["location", "tower", "lat", "long", "where"]) and len(numbers) == 1:
#         return [
#             {
#                 "$match": {
#                     "A_Party": numbers[0],
#                     "First_Lat": {"$exists": True}
#                 }
#             },
#             {
#                 "$project": {
#                     "A_Party": 1, "B_Party": 1, "SDateTime": 1,
#                     "First_Lat": 1, "First_Long": 1, "First_CGI": 1
#                 }
#             },
#             {"$sort": {"SDateTime": -1}},
#             {"$limit": 50}
#         ]
#
#     # ── RULE 8: IMEI lookup ────────────────────────────────────────────────────
#     if "imei" in q and len(numbers) == 1:
#         return [
#             {"$match": {"IMEI": numbers[0]}},
#             {"$sort": {"SDateTime": -1}},
#             {"$limit": 50}
#         ]
#
#     # ── RULE 9: IMSI lookup ────────────────────────────────────────────────────
#     if "imsi" in q and len(numbers) == 1:
#         return [
#             {"$match": {"IMSI": numbers[0]}},
#             {"$sort": {"SDateTime": -1}},
#             {"$limit": 50}
#         ]
#
#     # ── RULE 10: All records for a single phone number (fallback) ──────────────
#     if len(numbers) == 1:
#         return [
#             {"$match": {"A_Party": numbers[0]}},
#             {"$sort": {"SDateTime": -1}},
#             {"$limit": 50}
#         ]
#
#     return None  # No rule matched — let LLM handle it
#
#
# # ─── LLM PIPELINE BUILDER (fallback) ─────────────────────────────────────────
# def build_pipeline_prompt(user_query: str) -> str:
#     return f"""You are a MongoDB expert. Generate an aggregation pipeline as a JSON array.
# COLLECTION: CallDetailRecords
#
# SCHEMA (use ONLY these exact field names):
#   A_Party         (string)  — caller phone number
#   B_Party         (string)  — called number or service name
#   SDateTime       (ISODate) — call start datetime
#   EDateTime       (ISODate) — call end datetime
#   SDate           (ISODate) — call date only
#   STime           (string)  — call start time HH:MM:SS
#   Duration        (number)  — call duration in seconds
#   Call_Type       (string)  — SMS_IN, SMS_OUT, VOICE_IN, VOICE_OUT
#   FileServiceType (string)  — SMS or VOICE
#   FileCallType    (string)  — SMT, VOI, or DAT
#   IMEI            (string)  — device IMEI number
#   IMSI            (string)  — SIM IMSI number
#   First_Lat       (number)  — tower latitude
#   First_Long      (number)  — tower longitude
#   First_CGI       (string)  — tower cell global identity
#   Con_Type        (string)  — Pre (prepaid) or Post (postpaid)
#
# RULES:
# - Output ONLY a valid JSON array. No markdown, no backticks, no explanation.
# - Always add a {{"$limit": 50}} stage at the end.
# - Use ISODate-compatible datetime strings for date comparisons.
# - Do NOT invent field names. Use only the schema above.
#
# Query: {user_query}"""
#
#
# # ─── JSON PARSER ──────────────────────────────────────────────────────────────
# def clean_and_parse(raw: str):
#     if not raw:
#         return None
#     raw = re.sub(r"```json|```", "", raw).strip()
#     match = re.search(r"\[.*\]", raw, re.DOTALL)
#     if match:
#         try:
#             return json.loads(match.group(0))
#         except json.JSONDecodeError:
#             return None
#     return None
#
#
# # ─── RESULT FORMATTER ─────────────────────────────────────────────────────────
# def format_results(data: list, user_query: str) -> str:
#     """
#     For small result sets: formats directly without any LLM call (instant).
#     For large result sets: sends a sample to the LLM for a brief summary.
#     """
#     if not data:
#         return "No matching records found."
#
#     # ── Small results: format directly, no LLM ────────────────────────────────
#     if len(data) <= 5:
#         lines = [f"Found {len(data)} record(s):\n"]
#         for r in data:
#             # Aggregated result (e.g. top contacts)
#             if "_id" in r and "total_calls" in r:
#                 dur = r.get("total_duration", 0)
#                 lines.append(
#                     f"  {r['_id']:<20}  calls: {r['total_calls']}  "
#                     f"total duration: {dur}s ({dur // 60}m {dur % 60}s)"
#                 )
#             # Raw CDR record
#             else:
#                 sdt = r.get("SDateTime", "?")
#                 if hasattr(sdt, "strftime"):
#                     sdt = sdt.strftime("%Y-%m-%d %H:%M:%S")
#                 lines.append(
#                     f"  {str(r.get('A_Party','?')):<15} → {str(r.get('B_Party','?')):<20} "
#                     f"| {str(r.get('Call_Type','?')):<12} "
#                     f"| {sdt} "
#                     f"| {r.get('Duration', 0)}s"
#                 )
#         return "\n".join(lines)
#
#     # ── Large results: use LLM to summarize a sample ──────────────────────────
#     sample = data[:10]
#
#     # Serialize datetimes for JSON
#     def serialize(obj):
#         if isinstance(obj, datetime):
#             return obj.strftime("%Y-%m-%d %H:%M:%S")
#         return str(obj)
#
#     prompt = f"""You are a telecom intelligence analyst. Summarize these CDR results in 3-4 sentences.
# User query: {user_query}
# Total records found: {len(data)}
# Sample (first 10): {json.dumps(sample, default=serialize)}
# Be specific: mention numbers, dates, patterns if visible. Keep it concise."""
#
#     result = ask_llm(prompt, model=OLLAMA_MODEL_CHAT)
#     return result or f"Found {len(data)} records. Sample entry: {json.dumps(data[0], default=str)}"
#
#
# # ─── CORE PROCESSOR ───────────────────────────────────────────────────────────
# def process_query(user_query: str, history: list) -> str:
#     intent = detect_intent(user_query)
#
#     if intent == "general_chat":
#         return general_chat(user_query, history)
#
#     # Step 1: Try hardcoded rules (fastest — no LLM)
#     pipeline = force_rules(user_query)
#     if pipeline:
#         print(f"[Rule matched] Pipeline: {json.dumps(pipeline, default=str)}")
#     else:
#         # Step 2: Ask LLM to build the pipeline
#         print("--- No rule matched. Asking LLM to build pipeline... ---")
#         raw = ask_llm(build_pipeline_prompt(user_query), model=OLLAMA_MODEL_PIPELINE)
#         pipeline = clean_and_parse(raw)
#         if pipeline:
#             print(f"[LLM pipeline] {json.dumps(pipeline, default=str)}")
#
#     # Step 3: Last-resort fallback
#     if not pipeline:
#         print("--- Pipeline build failed. Using fallback. ---")
#         pipeline = [{"$sort": {"SDateTime": -1}}, {"$limit": 5}]
#
#     # Execute pipeline on MongoDB
#     try:
#         data = list(cdr_collection.aggregate(pipeline, maxTimeMS=20000))
#         return format_results(data, user_query)
#     except Exception as e:
#         return f"Error executing query: {e}"
#
#
# # ─── MAIN LOOP ────────────────────────────────────────────────────────────────
# if __name__ == "__main__":
#     chat_history = []
#     print("\n" + "=" * 50)
#     print("   CDR Intelligence Terminal")
#     print("=" * 50)
#     print("Sample queries:")
#     print("  • calls from 2024-11-09 to 2024-12-10 of number 7995300468")
#     print("  • top 10 contacts for 7995300468")
#     print("  • sms records for 7995300468")
#     print("  • calls between 7995300468 and 9876543210")
#     print("  • location of 7995300468")
#     print("  • imei 868350061246013")
#     print("Type 'exit' to quit.\n")
#
#     while True:
#         try:
#             user_input = input("User: ").strip()
#         except (EOFError, KeyboardInterrupt):
#             print("\nExiting.")
#             break
#
#         if user_input.lower() in ["exit", "quit"]:
#             print("Goodbye.")
#             break
#         if not user_input:
#             continue
#
#         response = process_query(user_input, chat_history)
#
#         # Only print response for non-streaming paths (streaming already printed)
#         if not any(kw in detect_intent(user_input) for kw in ["general_chat"]):
#             print(f"\nAssistant: {response}")
#
#         chat_history.append({"role": "user", "content": user_input})
#         chat_history.append({"role": "assistant", "content": response})