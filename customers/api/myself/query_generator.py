# import json
# import re
# import hashlib
# from functools import lru_cache
# from ollama import Client
#
# # ✅ Singleton client — created once, reused on every request
# _client = Client()
#
# system_prompt = """You are a MongoDB query generator. Output ONLY a raw JSON object. No markdown, no code fences, no explanation, no extra text, no comments.
#
# Collection: CallDetailRecords
# Fields: A_Party, B_Party, Duration, SDateTime, First_Lat, First_Long, IMEI, IMSI
#
# Rules:
# - Phone numbers are stored as strings in A_Party (caller) or B_Party (receiver)
# - For date ranges use $gte and $lte with ISO strings
# - For a summary/calls/records of a number, query A_Party only
# - Output must start with { and end with }
# - Do NOT add comments inside JSON
#
# Examples:
# User: show calls from 9876543210
# Output: {"A_Party":"9876543210"}
#
# User: calls from 9876543210 on 2024-03-01
# Output: {"A_Party":"9876543210","SDateTime":{"$gte":"2024-03-01T00:00:00Z","$lte":"2024-03-01T23:59:59Z"}}"""
#
#
# def _sanitize(text: str) -> str:
#     """Clean up common LLM formatting mistakes before JSON parsing."""
#     # Strip markdown fences
#     text = re.sub(r"```json|```", "", text).strip()
#     # Unescape double-braces (model echoes Python f-string style {{ }})
#     text = text.replace("{{", "{").replace("}}", "}")
#     # Remove inline JS/Python comments: // comment or # comment
#     # Only outside of strings — safe for typical LLM outputs
#     text = re.sub(r"(//[^\n\r\"]*|#[^\n\r\"]*)", "", text)
#     # Remove trailing commas before } or ]  e.g. {"a":1,}
#     text = re.sub(r",\s*([}\]])", r"\1", text)
#     return text.strip()
#
#
# def _extract_json(raw: str) -> dict:
#     """Try multiple strategies to extract a valid JSON object from LLM output."""
#
#     # Strategy 1: direct parse (fastest path — clean responses)
#     try:
#         return json.loads(raw.strip())
#     except json.JSONDecodeError:
#         pass
#
#     # Strategy 2: sanitize then parse
#     cleaned = _sanitize(raw)
#     try:
#         return json.loads(cleaned)
#     except json.JSONDecodeError:
#         pass
#
#     # Strategy 3: grab first {...} block from sanitized text
#     match = re.search(r"\{.*\}", cleaned, re.DOTALL)
#     if match:
#         try:
#             return json.loads(match.group())
#         except json.JSONDecodeError:
#             pass
#
#     raise ValueError(f"LLM did not return valid JSON.\nRaw response was:\n{repr(raw)}")
#
#
# def _hash_query(user_query: str) -> str:
#     return hashlib.md5(user_query.strip().lower().encode()).hexdigest()
#
#
# def _call_llm(user_query: str) -> dict:
#     """Call the LLM with up to 2 attempts before raising."""
#     last_error = None
#
#     for attempt in range(2):
#         response = _client.chat(
#             model="qwen2.5-coder:1.5b",  # swap to 7b if 1.5b keeps failing
#             messages=[
#                 {"role": "system", "content": system_prompt},
#                 {"role": "user", "content": user_query},
#             ],
#             options={
#                 "temperature": 0,
#                 "num_predict": 200,
#             },
#         )
#
#         raw = response["message"]["content"]
#         print(f"DEBUG LLM raw (attempt {attempt + 1}): {repr(raw)}")
#
#         try:
#             return _extract_json(raw)
#         except ValueError as e:
#             last_error = e
#             print(f"DEBUG parse failed attempt {attempt + 1}: {e}")
#
#     raise last_error
#
#
# # ✅ LRU cache — identical questions skip the LLM entirely
# @lru_cache(maxsize=256)
# def _cached_generate(query_hash: str, user_query: str) -> str:
#     # Cache stores JSON string (dicts aren't hashable for lru_cache)
#     result = _call_llm(user_query)
#     return json.dumps(result)
#
#
# def generate_query(user_query: str) -> dict:
#     query_hash = _hash_query(user_query)
#     return json.loads(_cached_generate(query_hash, user_query))