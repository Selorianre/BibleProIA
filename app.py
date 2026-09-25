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

    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"error": raw or str(e)}
        return data, e.code, dict(e.headers)

    except URLError as e:
        return {"error": str(e)}, 503, {}


def verifier_token_utilisateur(token):
    if not token:
        return None

    req = urllib_request.Request(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "apikey": SUPABASE_SECRET_KEY,
            "Authorization": f"Bearer {token}",
        },
        method="GET"
    )

    try:
        with urllib_request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def utilisateur_est_admin(user_id):
    encoded_id = urllib_parse.quote(user_id, safe="")
    data, status, _ = supabase_http(
        f"/rest/v1/admins?user_id=eq.{encoded_id}&select=user_id"
    )

    return status == 200 and isinstance(data, list) and len(data) > 0


def admin_required(view_function):
    @wraps(view_function)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Authentification requise."}), 401

        token = auth_header[7:].strip()
        user = verifier_token_utilisateur(token)

        if not user or not user.get("id"):
            return jsonify({"error": "Session invalide ou expirée."}), 401

        if not utilisateur_est_admin(user["id"]):
            return jsonify({"error": "Accès administrateur interdit."}), 403

        request.admin_user = user
        return view_function(*args, **kwargs)

    return wrapper


def compter_table(table):
    _, status, headers = supabase_http(
        f"/rest/v1/{table}?select=id",
        method="HEAD",
        headers={"Prefer": "count=exact"}
    )

    if status not in (200, 204):
        return None

    content_range = (
        headers.get("Content-Range")
        or headers.get("content-range")
        or ""
    )

    if "/" in content_range:
        total = content_range.rsplit("/", 1)[-1]
        if total.isdigit():
            return int(total)

    return None


# ============================================================
# BASE DE CONNAISSANCES
# ============================================================

def charger_connaissances():
    documents = []

    if not KNOWLEDGE_DIR.exists():
        print(f"Attention : dossier de connaissances introuvable : {KNOWLEDGE_DIR}")
        return documents

    for fichier in KNOWLEDGE_DIR.rglob("*.txt"):
        try:
            contenu = fichier.read_text(encoding="utf-8").strip()

            if not contenu:
                continue

            morceaux = re.split(r"\n\s*\n", contenu)

            for morceau in morceaux:
                morceau = morceau.strip()

                if len(morceau) >= 30:
                    documents.append({
                        "fichier": str(fichier),
                        "texte": morceau[:2500]
                    })

        except Exception as e:
            print(f"Erreur avec {fichier}: {e}")

    print(f"{len(documents)} morceaux de connaissances chargés.")
    return documents


CONNAISSANCES = charger_connaissances()


def rechercher_connaissances(question, nombre=3):
    mots = {
        mot
        for mot in re.findall(r"\w+", question.lower())
        if len(mot) >= 3
    }

    resultats = []

    for document in CONNAISSANCES:
        texte = document["texte"].lower()
        score = sum(1 for mot in mots if mot in texte)

        if score > 0:
            resultats.append((score, document))

    resultats.sort(key=lambda x: x[0], reverse=True)

    return [
        document
        for score, document in resultats[:nombre]
    ]


# ============================================================
# RECHERCHE INTERNET
# ============================================================

def rechercher_internet(question, nombre=4):
    resultats = []

    try:
        moteur = DDGS()
        recherches = moteur.text(question, max_results=nombre)

        for resultat in recherches:
            url = resultat.get("href", "")

            if not url:
                continue

            resultats.append({
                "titre": resultat.get("title", ""),
                "url": url,
                "description": resultat.get("body", "")[:600]
            })

    except Exception as e:
        print(f"Erreur recherche Internet : {e}")

    return resultats


# ============================================================
# API ADMIN
# ============================================================

@app.route("/api/admin/stats")
@admin_required
def admin_stats():
    users_data, users_status, _ = supabase_http(
        "/auth/v1/admin/users?page=1&per_page=1"
    )

    total_users = None
    if users_status == 200 and isinstance(users_data, dict):
        total_users = users_data.get("total")

    return jsonify({
        "users": total_users,
        "conversations": compter_table("conversations"),
        "messages": compter_table("messages"),
        "knowledge_chunks": len(CONNAISSANCES),
        "model": MODEL,
    })


@app.route("/api/admin/users")
@admin_required
def admin_users():
    search = request.args.get("search", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 25, type=int), 1), 100)

    path = f"/auth/v1/admin/users?page={page}&per_page={per_page}"

    data, status, _ = supabase_http(path)

    if status != 200:
        return jsonify({
            "error": "Impossible de charger les utilisateurs.",
            "details": data
        }), status

    users = data.get("users", []) if isinstance(data, dict) else []

    if search:
        search_lower = search.lower()
        users = [
            user for user in users
            if search_lower in (user.get("email") or "").lower()
        ]

    safe_users = [{
        "id": user.get("id"),
        "email": user.get("email"),
        "created_at": user.get("created_at"),
        "last_sign_in_at": user.get("last_sign_in_at"),
        "email_confirmed_at": user.get("email_confirmed_at"),
    } for user in users]

    return jsonify({
        "users": safe_users,
        "total": data.get("total") if isinstance(data, dict) else len(safe_users),
        "page": page,
    })


@app.route("/api/admin/system")
@admin_required
def admin_system():
    txt_files = list(KNOWLEDGE_DIR.rglob("*.txt")) if KNOWLEDGE_DIR.exists() else []

    return jsonify({
        "service": "BibleProIA",
        "status": "online",
        "model": MODEL,
        "knowledge_files": len(txt_files),
        "knowledge_chunks": len(CONNAISSANCES),
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_SECRET_KEY),
        "gemini_configured": bool(API_KEY),
        "server_time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/admin/knowledge")
@admin_required
def admin_knowledge():
    files = []

    if KNOWLEDGE_DIR.exists():
        for path in sorted(KNOWLEDGE_DIR.rglob("*.txt")):
            try:
                files.append({
                    "name": path.name,
                    "relative_path": str(path.relative_to(KNOWLEDGE_DIR)),
                    "size": path.stat().st_size,
                })
            except OSError:
                pass

    return jsonify({
        "files": files,
        "file_count": len(files),
        "chunks": len(CONNAISSANCES),
    })


# ============================================================
# MANIFEST
# ============================================================

@app.route("/manifest.json")
def manifest():
    manifest_path = Path(__file__).resolve().parent / "manifest.json"

    if not manifest_path.exists():
        return "Erreur : manifest.json introuvable.", 404

    return send_from_directory(
        manifest_path.parent,
        manifest_path.name,
        mimetype="application/manifest+json"
    )


# ============================================================
# PAGE PRINCIPALE
# ============================================================

@app.route("/", methods=["GET", "POST"])
def index():
    reponse = ""
    sources = []

    if request.method == "POST":
        question = request.form.get("question", "").strip()

        if question:
            try:
                resultats_web = rechercher_internet(question)
                contexte_web = ""

                for numero, resultat in enumerate(resultats_web, start=1):
                    contexte_web += f"""
SOURCE WEB {numero}

Titre : {resultat['titre']}
URL : {resultat['url']}
Résumé : {resultat['description']}
"""
                    sources.append(resultat)

                resultats_locaux = rechercher_connaissances(question)
                contexte_local = ""

                for document in resultats_locaux:
                    contexte_local += f"""
DOCUMENT LOCAL

Fichier : {document['fichier']}

{document['texte']}
"""

                message = f"""
QUESTION :

{question}


SOURCES INTERNET :

{contexte_web}


BASE DE CONNAISSANCES :

{contexte_local}


Réponds à la question.

Utilise les sources Internet en priorité.

Utilise la base locale lorsqu'elle apporte
une information pertinente.

Ne crée aucune source qui n'est pas présente
dans le contexte.
"""

                resultat = client.models.generate_content(
                    model=MODEL,
                    contents=message,
                    config={
                        "system_instruction": SYSTEM_PROMPT,
                        "max_output_tokens": 500
                    }
                )

                reponse = resultat.text

            except Exception as e:
                print(f"Erreur pendant la génération : {e}")
                reponse = "Une erreur est survenue pendant la génération de la réponse."

    return render_template(
        "index.html",
        reponse=reponse,
        sources=sources
    )


if __name__ == "__main__":
    app.run(debug=True)
