"""Télémétrie de l'exporteur v2 (Metaprogramme-lean).

Lit `export_v2.telemetry.json` écrit périodiquement par l'exporteur
(PhiAstV2.lean, toutes les 1000 déclarations) et affiche l'état INTERNE :
quelle déclaration est en cours, taille des tables, doublons trouvés.

L'instrument lit, il n'interprète pas : pas de projection, pas de
diagnostic caché. Ce que le JSON dit, c'est ce qu'on affiche.

Ordre Tomy (2026-10-06).
"""

import json
import os
import time

NOM_FICHIER_DEFAUT = "export_v2.telemetry.json"

CHAMPS_ATTENDUS = (
    "decls_done",
    "current_decl",
    "term_table_size",
    "str_table_size",
    "bytes_written",
    "dedup_hits",
    "timestamp_ms",
)


def trouver_telemetry(depart=".", nom=NOM_FICHIER_DEFAUT):
    """Cherche le fichier télémétrie en remontant depuis `depart`.

    Retourne le chemin absolu ou None.
    """
    chemin = os.path.abspath(depart)
    while True:
        candidat = os.path.join(chemin, nom)
        if os.path.isfile(candidat):
            return candidat
        parent = os.path.dirname(chemin)
        if parent == chemin:
            return None
        chemin = parent


def lire_telemetry(chemin):
    """Lit et valide le JSON de télémétrie.

    Retourne (dict, None) ou (None, message_erreur).
    """
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            donnees = json.load(f)
    except OSError as e:
        return None, "fichier illisible : %s" % e
    except ValueError as e:
        return None, "JSON invalide : %s" % e
    if not isinstance(donnees, dict):
        return None, "le JSON n'est pas un objet"
    return donnees, None


def age_secondes(donnees):
    """Âge de la télémétrie en secondes (None si pas de timestamp)."""
    ts = donnees.get("timestamp_ms")
    if ts is None:
        return None
    try:
        return max(0.0, (time.time() * 1000 - float(ts)) / 1000.0)
    except (TypeError, ValueError):
        return None


def taux_dedup_estime(donnees):
    """Taux de déduplication estimé = dedup_hits / (dedup_hits + term_table_size).

    None si les données manquent. C'est une estimation, pas une mesure
    exacte : les hits comptent les réutilisations, la table compte les
    termes uniques mémorisés.
    """
    hits = donnees.get("dedup_hits")
    table = donnees.get("term_table_size")
    try:
        hits = int(hits)
        table = int(table)
    except (TypeError, ValueError):
        return None
    total = hits + table
    if total <= 0:
        return None
    return hits / total


def formater_octets(n):
    """Formate un nombre d'octets (Mo/Go)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n >= 1e9:
        return "%.2f Go" % (n / 1e9)
    if n >= 1e6:
        return "%.1f Mo" % (n / 1e6)
    return "%d o" % int(n)


def afficher(donnees, chemin, total_attendu=208018):
    """Affiche la télémétrie. Retourne 0."""
    decls = donnees.get("decls_done", "?")
    decl_nom = donnees.get("current_decl", "?")
    term_t = donnees.get("term_table_size", "?")
    str_t = donnees.get("str_table_size", "?")
    octets = donnees.get("bytes_written", "?")
    hits = donnees.get("dedup_hits", "?")

    print("=== Telemetry export v2 ===")
    print("fichier : %s" % chemin)
    print("")
    print("declaration en cours : %s" % decl_nom)
    try:
        pct = float(decls) / total_attendu * 100
        print("progression : %s / %d (%.1f %%)" % (decls, total_attendu, pct))
    except (TypeError, ValueError):
        print("progression : %s / %d" % (decls, total_attendu))
    print("octets ecrits : %s" % formater_octets(octets))
    print("")
    print("table termes (hash-consing) : %s entrees" % term_t)
    print("table chaines (internage)   : %s entrees" % str_t)
    print("doublons trouves            : %s" % hits)
    taux = taux_dedup_estime(donnees)
    if taux is not None:
        print("taux dedup estime           : %.1f %%" % (taux * 100))
    age = age_secondes(donnees)
    if age is not None:
        if age < 120:
            print("fraicheur                   : il y a %.0f s" % age)
        else:
            print("fraicheur                   : il y a %.1f min (PEUT-ETRE PERIME)"
                  % (age / 60))
    print("")
    print("Note : le taux de dedup est une estimation "
          "(hits / (hits + table)).")
    return 0
