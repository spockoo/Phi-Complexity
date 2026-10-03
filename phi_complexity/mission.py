"""
mission.py — Registre « but de mission » (garde anti-divergence, 2026-10-01).

Directive unique (Tomy) : démontrer formellement la conjecture de
Navier-Stokes suivant les critères de l'institut Clay.
Troisième directive (Tomy, 2026-10-01) : développer phi-complexity
librement SOUS CONDITION que ça serve la directive unique.

Règle MÉCANIQUE (testée dans tests/test_mission.py, pas disciplinaire) :
chaque module de `phi_complexity/` porte une justification explicite —
quel sorry du Master il sert, ou en quoi il durcit la chaîne
d'instruments vers la démonstration. Un module ajouté sans justification
fait échouer le test. Léger par design : un tag (`sert`) + une phrase
(`but`), pas un formulaire.

`sert` ∈ SORRYS_MASTER ∪ {"tous", "instrument"} :
- un des 5 sorrys : le module sert directement ce trou du Master ;
- "tous" : le module sert la chaîne des 5 sorrys dans son ensemble
  (veille, priorisation des trous, registre des hypothèses…) ;
- "instrument" : le module durcit l'instrument lui-même (exactitude,
  traçabilité, interface, socle) — la confiance dans l'outil est
  vérifiée, jamais la foi (doctrine Tomy).
"""

import os

# ────────────────────────────────────────────────────────
# VOCABULAIRE CONTRÔLÉ
# ────────────────────────────────────────────────────────

#: Les 5 sorrys protégés du Master (Clay_NS_Master.lean) — le nord.
SORRYS_MASTER = (
    "local_existence",
    "local_uniqueness",
    "energy_identity",
    "leray_existence",
    "BKM_criterion",
)

#: Valeurs autorisées pour le champ `sert`.
SERT_AUTORISES = SORRYS_MASTER + ("tous", "instrument")

#: Longueur minimale d'une justification (anti-remplissage).
BUT_MIN_LONGUEUR = 20


# ────────────────────────────────────────────────────────
# REGISTRE — module -> {"but": ..., "sert": ...}
# ────────────────────────────────────────────────────────

JUSTIFICATIONS = {
    "analyseur": {
        "but": "Alias de compatibilité ascendante (0.1.x) : ne pas casser "
               "les usages existants de l'instrument.",
        "sert": "instrument",
    },
    "ancrage": {
        "but": "Coud la lentille entropique à la chaîne des sondes : le "
               "resserrement chiffré alimente la piste des hypothèses vers "
               "les sorrys.",
        "sert": "tous",
    },
    "bayes": {
        "but": "Moteur d'inférence qui nourrit la priorisation bayésienne "
               "des trous à attaquer en premier.",
        "sert": "tous",
    },
    "carte": {
        "but": "Index des symboles + santé phi en une passe : la matière "
               "première que la veille compare aux références.",
        "sert": "tous",
    },
    "cli": {
        "but": "Interface en ligne de commande : la surface par laquelle "
               "l'instrument est opéré et audité.",
        "sert": "instrument",
    },
    "core": {
        "but": "Constantes et version : le socle partagé, une seule "
               "source de vérité sur l'identité de l'outil.",
        "sert": "instrument",
    },
    "croyances": {
        "but": "« Attaquer en premier » : classe les symboles à trou "
               "(sorry/admit) par postérieur pour ordonner l'assaut sur "
               "les 5 sorrys.",
        "sert": "tous",
    },
    "durcissement": {
        "but": "Batterie de sabotages contrôlés + red-team : l'instrument "
               "échoue bruyamment, jamais en silence (doctrine Tomy).",
        "sert": "instrument",
    },
    "editeur": {
        "but": "Indexeur de fichiers (sous-paquet) : l'analyse source "
               "dont vivent carte, veille et chemins.",
        "sert": "instrument",
    },
    "eft": {
        "but": "Arithmétique exacte (Error-Free Transformations, opt-in) : "
               "les mesures de l'instrument ne doivent pas introduire "
               "de faux calculs.",
        "sert": "instrument",
    },
    "entropie": {
        "but": "Lentille entropique sur la sonde B : chiffre en bits le "
               "resserrement de l'espace des approches vers "
               "l'inconditionnel.",
        "sert": "tous",
    },
    "explorateur": {
        "but": "Surface unifiée (typographie des statuts typés + analyse) : "
               "lire l'état de la chaîne d'un seul regard.",
        "sert": "instrument",
    },
    "formules": {
        "but": "Parser de formules (sous-paquet) : lire les expressions "
               "de complexité sans les déformer.",
        "sert": "instrument",
    },
    "langs": {
        "but": "Analyseurs par langage, dont Lean : lire les .lean des "
               "5 sorrys est le cœur du métier de l'instrument.",
        "sert": "tous",
    },
    "metriques": {
        "but": "Indice de radiance et métriques : mesurer sans juger, "
               "pour ordonner l'attention, jamais décider.",
        "sert": "instrument",
    },
    "mission": {
        "but": "Le présent registre : la garde anti-divergence elle-même, "
               "vérifiée par test.",
        "sert": "instrument",
    },
    "modeles": {
        "but": "Structures de données communes aux analyseurs : un seul "
               "vocabulaire pour tous les langages.",
        "sert": "instrument",
    },
    "oracle": {
        "but": "Traces de raisonnement exhibées : chaque mise à jour de "
               "croyance montre sa chaîne, pas de boîte noire.",
        "sert": "instrument",
    },
    "parseur_autonome": {
        "but": "Parseur Lean 4 proprietaire de phi-complexity (stdlib "
               "uniquement, zero dependance externe) : lexer + descente "
               "recursive sur les en-tetes + delimitation exacte des corps. "
               "Remplace tree-sitter sur tout le chemin Lean depuis "
               "l'autonomie stricte du 2026-10-03.",
        "sert": "instrument",
    },
    "piste_sorry": {
        "but": "Piste d'un sorry à travers Lean 4 : inventaire rigoureux "
               "(commentaires et chaînes exclus), graphe d'imports, chantiers "
               "du registre aux statuts typés — le chemin depuis n'importe "
               "quelle complexité vers la décharge.",
        "sert": "tous",
    },
    "chemins_verifiables": {
        "but": "Chemins vérifiables vers un sorry : candidats de câblage "
               "guidés (jamais de source contenant un sorry), chacun identifié "
               "formellement comme une preuve — un fichier Lean que Lean "
               "tranche (PROUVÉ / RÉFUTÉ avec diagnostic / INDÉCIDÉ). "
               "L'instrument propose, Lean dispose. Extension multi-lemmes "
               "(2026-10-01) : chaînage borné par l'inégalité de budget "
               "C(P) ≤ B(S), fragment D vérifiable, INDÉCIDÉ a priori.",
        "sert": "tous",
    },
    "rapport": {
        "but": "Restitution (console/Markdown) : l'instrument montre, "
               "il n'automatise pas le jugement.",
        "sert": "instrument",
    },
    "sondes": {
        "but": "Sondes A/B : lisent le registre vivant des hypothèses "
               "(statuts typés) et tracent l'inconditionnel à partir du "
               "conditionnel — la carte des 5 sorrys.",
        "sert": "tous",
    },
    "veille": {
        "but": "Moniteur de régression structurelle : détecte toute "
               "dégradation silencieuse autour des 5 sorrys (STABLE / "
               "DÉGRADATION DÉTECTÉE / INSTRUMENT DÉGRADÉ).",
        "sert": "tous",
    },
    "dependances": {
        "but": "Graphe de dépendances Lean : extraction exacte des "
               "dépendances entre définitions (fermeture transitive, tri "
               "topologique). Élimine le danger d'oubli d'une dépendance "
               "lors de l'extraction d'un module — chaque lemme porte "
               "ses dépendances exactes, vérifiées mécaniquement.",
        "sert": "instrument",
    },
    "parseur_lean": {
        "but": "Parseur Lean robuste : extrait les 196 déclarations d'un "
               "fichier de toute taille par regex (pas de limite tree-sitter). "
               "Corrige l'échec silencieux sur fichiers >5000 lignes — "
               "l'instrument voit tout, il ne rate rien.",
        "sert": "instrument",
    },
}


# ────────────────────────────────────────────────────────
# DÉCOUVERTE + VALIDATION (le mécanisme de la garde)
# ────────────────────────────────────────────────────────

def _repertoire_paquet() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def modules_couverts() -> list:
    """Modules soumis à la garde : .py de premier niveau (hors dunder)
    + sous-paquets (dossier avec __init__.py)."""
    rep = _repertoire_paquet()
    modules = []
    for nom in sorted(os.listdir(rep)):
        chemin = os.path.join(rep, nom)
        if nom.endswith(".py") and not nom.startswith("__"):
            modules.append(nom[:-3])
        elif (os.path.isdir(chemin) and not nom.startswith("__")
              and os.path.isfile(os.path.join(chemin, "__init__.py"))):
            modules.append(nom)
    return modules


def valider(modules=None) -> list:
    """Valide le registre contre une liste de modules.

    Retourne la liste des problèmes (chaînes lisibles) ; [] = garde OK.
    `modules=None` → découvre l'arbre réel du paquet.
    """
    if modules is None:
        modules = modules_couverts()
    problemes = []
    for mod in modules:
        entree = JUSTIFICATIONS.get(mod)
        if entree is None:
            problemes.append(
                f"module '{mod}' SANS justification de mission — "
                f"ajoutez une entrée dans mission.py (but + sert)")
            continue
        but = entree.get("but", "")
        if not isinstance(but, str) or len(but.strip()) < BUT_MIN_LONGUEUR:
            problemes.append(
                f"module '{mod}' : justification trop courte ou absente "
                f"(min {BUT_MIN_LONGUEUR} caractères)")
        sert = entree.get("sert")
        valeurs = list(sert) if isinstance(sert, (list, tuple)) else [sert]
        for v in valeurs:
            if v not in SERT_AUTORISES:
                problemes.append(
                    f"module '{mod}' : sert={v!r} hors vocabulaire "
                    f"(autorisé : {', '.join(SERT_AUTORISES)})")
    for mod in JUSTIFICATIONS:
        if mod not in modules:
            problemes.append(
                f"entrée orpheline '{mod}' : le module n'existe plus — "
                f"nettoyez mission.py")
    return problemes
