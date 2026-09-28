from flask import Flask, render_template, request, send_from_directory, jsonify
from google import genai
from ddgs import DDGS
from pathlib import Path
from urllib import request as urllib_request
from urllib import parse as urllib_parse
from urllib.error import HTTPError, URLError
from functools import wraps
from datetime import datetime, timezone
import json
import os
import re

app = Flask(__name__)

MODEL = "gemini-3.5-flash-lite"
KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

API_KEY = os.environ.get("GEMINI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")

# Dépôt GitHub utilisé pour le journal automatique des nouveautés.
GITHUB_OWNER = "Selorianre"
GITHUB_REPO = "BibleProIA"
GITHUB_BRANCH = "main"

if not API_KEY:
    raise RuntimeError("La variable GEMINI_API_KEY n'est pas configurée.")

if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError(
        "SUPABASE_URL et SUPABASE_SECRET_KEY doivent être configurées dans Render."
    )

client = genai.Client(api_key=API_KEY)


# ============================================================
# INSTRUCTIONS DE L'IA
# ============================================================

SYSTEM_PROMPT = """
Tu es BibleProIA, une intelligence artificielle chrétienne
spécialisée dans la Bible et particulièrement dans l'adventisme
du septième jour.

Réponds en français quand ils parlent en français et en anglais quands ils parlent anglais.

Utilise principalement les informations fournies dans les sources
Internet et dans la base de connaissances locale.

Règles :
- Réponds clairement et directement.
- Ne fabrique jamais une source ou une référence biblique.
- Distingue le texte biblique des interprétations chrétiennes.
- Lorsque tu présentes une position adventiste, indique clairement
  qu'il s'agit de la compréhension adventiste.
- Si les informations sont insuffisantes, dis-le.
- Ne prétends pas avoir consulté une page entière si seul son résumé
  t'a été fourni.
"""


# ============================================================
# OUTILS SUPABASE SERVEUR
# ============================================================

def supabase_http(path, method="GET", body=None, headers=None):
    url = f"{SUPABASE_URL}{path}"

    final_headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    if headers:
        final_headers.update(headers)

    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")

    req = urllib_request.Request(
        url,
        data=payload,
        headers=final_headers,
        method=method
    )

    try:
        with urllib_request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else None
            return data, response.status, dict(response.headers)
