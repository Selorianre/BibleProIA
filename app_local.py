from flask import Flask, render_template, request
from ollama import chat
from ddgs import DDGS
from pathlib import Path
import re

app = Flask(__name__)

MODEL = "llama3.2:3b"
KNOWLEDGE_DIR = Path("knowledge")


SYSTEM_PROMPT = """
Tu es BibleProIA, une intelligence artificielle chrétienne spécialisée
dans la Bible et particulièrement dans l'adventisme du septième jour.

Tu dois répondre en français.

Tes sources principales sont les résultats Internet fournis dans le contexte.
La base de connaissances locale est une source complémentaire.

Règles :
- Réponds clairement et directement.
- Utilise les sources fournies plutôt que tes connaissances générales.
- Ne fabrique jamais une source, une citation ou une référence biblique.
- Distingue les textes bibliques des interprétations chrétiennes.
- Lorsque tu présentes une position adventiste, indique clairement qu'il s'agit
  de la compréhension adventiste.
- Si les sources sont insuffisantes, dis-le.
- Ne prétends pas avoir consulté une page Internet entière si seul son résumé
  t'a été fourni.
"""


# ============================================================
# CHARGEMENT DE LA BASE LOCALE UNE SEULE FOIS
# ============================================================

def charger_connaissances():

    documents = []

    for fichier in KNOWLEDGE_DIR.rglob("*.txt"):

        try:

            contenu = fichier.read_text(
                encoding="utf-8"
            ).strip()

            if not contenu:
                continue

            # On découpe les gros documents en morceaux
            morceaux = re.split(
                r"\n\s*\n",
                contenu
            )

            for morceau in morceaux:

                morceau = morceau.strip()

                if len(morceau) < 30:
                    continue

                documents.append({
                    "fichier": str(fichier),
                    "texte": morceau[:2500]
                })

        except Exception as e:

            print(
                f"Erreur lecture {fichier}: {e}"
            )

    print(
        f"{len(documents)} morceaux de connaissances chargés."
    )

    return documents


CONNAISSANCES = charger_connaissances()


# ============================================================
# RECHERCHE DANS TES CONNAISSANCES
# ============================================================

def rechercher_connaissances(question, nombre=3):

    mots = {
        mot.lower()
        for mot in re.findall(
            r"\w+",
            question.lower()
        )
        if len(mot) >= 3
    }

    resultats = []

    for document in CONNAISSANCES:

        texte = document["texte"].lower()

        score = 0

        for mot in mots:

            if mot in texte:
                score += 1

        if score > 0:

            resultats.append(
                (
                    score,
                    document
                )
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

def rechercher_internet(question, nombre=5):

    resultats = []

    try:

        moteur = DDGS()

        recherches = moteur.text(
            question,
            max_results=nombre
        )

        for resultat in recherches:

            titre = resultat.get(
                "title",
                ""
            )

            url = resultat.get(
                "href",
                ""
            )

            description = resultat.get(
                "body",
                ""
            )

            if not url:
                continue

            resultats.append({
                "titre": titre,
                "url": url,
                "description": description[:1000]
            })

    except Exception as e:

        print(
            f"Erreur recherche Internet : {e}"
        )

    return resultats


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
                # INTERNET = SOURCE PRINCIPALE
                # ------------------------------------------------

                resultats_web = rechercher_internet(
                    question,
                    nombre=5
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

                    sources.append(
                        resultat
                    )


                # ------------------------------------------------
                # BASE PERSONNELLE = COMPLÉMENT
                # ------------------------------------------------

                resultats_locaux = rechercher_connaissances(
                    question,
                    nombre=3
                )

                contexte_local = ""

                for document in resultats_locaux:

                    contexte_local += f"""
DOCUMENT LOCAL
Fichier : {document['fichier']}
Contenu :
{document['texte']}
"""


                # ------------------------------------------------
                # CONTEXTE ENVOYÉ À LLAMA
                # ------------------------------------------------

                contexte = f"""
========================
SOURCES INTERNET
========================

{contexte_web}


========================
BASE DE CONNAISSANCES
========================

{contexte_local}
"""


                message = f"""
QUESTION DE L'UTILISATEUR :

{question}


INFORMATIONS DISPONIBLES :

{contexte}


INSTRUCTIONS :

Réponds à la question en utilisant prioritairement
les informations provenant des sources Internet.

Utilise la base locale uniquement lorsqu'elle apporte
une information pertinente.

Si une source Internet semble peu fiable ou contradictoire,
indique-le.

À la fin, indique les sources utilisées sous la forme :

Sources :
- Nom de la source
- Nom de la source
"""


                # ------------------------------------------------
                # LLAMA
                # ------------------------------------------------

                resultat = chat(

                    model=MODEL,

                    messages=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT
                        },
                        {
                            "role": "user",
                            "content": message
                        }
                    ]
                )


                reponse = resultat.message.content


            except Exception as e:

                reponse = (
                    "Une erreur est survenue : "
                    + str(e)
                )


    return render_template(
        "index.html",
        reponse=reponse,
        sources=sources
    )


# ============================================================
# DÉMARRAGE
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )