from flask import Flask, render_template, request, send_from_directory
from google import genai
from ddgs import DDGS
from pathlib import Path
import os
import re

app = Flask(__name__)

MODEL = "gemini-3.5-flash-lite"
KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "La variable GEMINI_API_KEY n'est pas configurée."
    )

client = genai.Client(api_key=API_KEY)


# ============================================================
# INSTRUCTIONS DE L'IA
# ============================================================

SYSTEM_PROMPT = """
Tu es BibleProIA, une intelligence artificielle chrétienne
spécialisée dans la Bible et particulièrement dans l'adventisme
du septième jour.

Réponds toujours en français.

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
# CHARGEMENT DE LA BASE DE CONNAISSANCES
# ============================================================

def charger_connaissances():

    documents = []

    if not KNOWLEDGE_DIR.exists():
        print(
            f"Attention : dossier de connaissances introuvable : "
            f"{KNOWLEDGE_DIR}"
        )
        return documents

    for fichier in KNOWLEDGE_DIR.rglob("*.txt"):

        try:

            contenu = fichier.read_text(
                encoding="utf-8"
            ).strip()

            if not contenu:
                continue

            morceaux = re.split(
                r"\n\s*\n",
                contenu
            )

            for morceau in morceaux:

                morceau = morceau.strip()

                if len(morceau) >= 30:

                    documents.append({
                        "fichier": str(fichier),
                        "texte": morceau[:2500]
                    })

        except Exception as e:

            print(
                f"Erreur avec {fichier}: {e}"
            )

    print(
        f"{len(documents)} morceaux de connaissances chargés."
    )

    return documents


CONNAISSANCES = charger_connaissances()


# ============================================================
# RECHERCHE DANS LA BASE LOCALE
# ============================================================

def rechercher_connaissances(question, nombre=3):

    mots = {
        mot
        for mot in re.findall(
            r"\w+",
            question.lower()
        )
        if len(mot) >= 3
    }

    resultats = []

    for document in CONNAISSANCES:

        texte = document["texte"].lower()

        score = sum(
            1
            for mot in mots
            if mot in texte
        )

        if score > 0:

            resultats.append(
                (score, document)
            )

    resultats.sort(
        key=lambda x: x[0],
        reverse=True
    )

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

        recherches = moteur.text(
            question,
            max_results=nombre
        )

        for resultat in recherches:

            url = resultat.get(
                "href",
                ""
            )

            if not url:
                continue

            resultats.append({
                "titre": resultat.get(
                    "title",
                    ""
                ),
                "url": url,
                "description": resultat.get(
                    "body",
                    ""
                )[:600]
            })

    except Exception as e:

        print(
            f"Erreur recherche Internet : {e}"
        )

    return resultats


# ============================================================
# MANIFEST POUR L'APPLICATION ANDROID / PWA
# ============================================================

@app.route("/manifest.json")
def manifest():

    manifest_path = (
        Path(__file__).resolve().parent
        / "manifest.json"
    )

    if not manifest_path.exists():

        return (
            "Erreur : manifest.json introuvable.",
            404
        )

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

        question = request.form.get(
            "question",
            ""
        ).strip()

        if question:

            try:

                # ------------------------------------------------
                # RECHERCHE INTERNET
                # ------------------------------------------------

                resultats_web = rechercher_internet(
                    question
                )

                contexte_web = ""

                for numero, resultat in enumerate(
                    resultats_web,
                    start=1
                ):

                    contexte_web += f"""
SOURCE WEB {numero}

Titre : {resultat['titre']}
URL : {resultat['url']}
Résumé : {resultat['description']}
"""

                    sources.append(resultat)


                # ------------------------------------------------
                # RECHERCHE BASE DE CONNAISSANCES
                # ------------------------------------------------

                resultats_locaux = rechercher_connaissances(
                    question
                )

                contexte_local = ""

                for document in resultats_locaux:

                    contexte_local += f"""
DOCUMENT LOCAL

Fichier : {document['fichier']}

{document['texte']}
"""


                # ------------------------------------------------
                # MESSAGE ENVOYÉ À GEMINI
                # ------------------------------------------------

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


                # ------------------------------------------------
                # APPEL GEMINI
                # ------------------------------------------------

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

                print(
                    f"Erreur pendant la génération : {e}"
                )

                reponse = (
                    "Erreur : "
                    + str(e)
                )


    return render_template(
        "index.html",
        reponse=reponse,
        sources=sources
    )


# ============================================================
# LANCEMENT LOCAL
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )
```
