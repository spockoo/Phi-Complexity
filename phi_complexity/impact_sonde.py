"""impact_sonde — analyse d'impact native intégrée à ``phi sonde`` (PHI-NATIF-B).

Quand on sonde un mécanisme dont le nom correspond à un symbole Python
(fonction, classe, méthode, module), cette section calcule automatiquement :

- les **dépendants** via ``impact_avant`` (« si je change ce symbole,
  qu'est-ce qui casse ? ») ;
- le **score de risque** de modification (0-100) et son niveau
  (FAIBLE / MOYEN / ÉLEVÉ / CRITIQUE) via ``score_risque``.

Racine analysée par défaut : le paquet ``phi_complexity`` lui-même
(l'instrument s'analyse lui-même en priorité).

Performance : le graphe est construit UNE SEULE FOIS par invocation
(``GrapheDependances.depuis_repertoire``), puis réutilisé pour le
symbole sondé — jamais reconstruit par symbole.

Dégradation gracieuse : toute erreur (construction du graphe, symbole
introuvable, nom non-Python comme « H29 » ou « 138 ») produit un
dictionnaire avec ``avertissement`` renseigné — jamais d'exception
propagée vers le CLI.
"""

from __future__ import annotations

import os

# Nombre maximal d'identifiants de dépendants inclus dans la sortie JSON
# (le total exact reste dans ``dependants["nombre"]``).
_LIMITE_LISTE_DEPENDANTS = 50

# Nombre maximal de candidats alternatifs listés quand le nom est ambigu.
_LIMITE_CANDIDATS = 10


def racine_impact_par_defaut() -> str:
    """Racine analysée par défaut : le répertoire du paquet phi_complexity."""
    return os.path.dirname(os.path.abspath(__file__))


def _nom_module_pointe(relpath: str) -> str:
    """'a/b/c.py' -> 'a.b.c' ; 'a/b/__init__.py' -> 'a.b'.

    Miroir de ``GrapheDependances._nom_module`` (graphe.py) pour pouvoir
    résoudre un nom pointé « module.symbole » sans toucher aux privés.
    """
    sans_ext = relpath[:-3] if relpath.endswith(".py") else relpath
    if os.path.basename(sans_ext) == "__init__":
        sans_ext = os.path.dirname(sans_ext)
    return sans_ext.replace(os.sep, ".")


def trouver_symboles(graphe, nom: str, limite: int = _LIMITE_CANDIDATS) -> list[str]:
    """Retrouve les nœuds du graphe correspondant au nom sondé.

    Ordre de priorité (déterministe, trié par nid à priorité égale) :
    1. identifiant exact de nœud (ex. ``sondes.py:sonder``) ;
    2. qualname exact (ex. ``sonder``, ``RegistreSondes.charger``) ;
    3. nom pointé module.symbole (ex. ``sondes.sonder``).

    Retourne au plus ``limite`` identifiants, le premier étant le
    candidat principal. À priorité égale, les nœuds synthétiques
    « externe:… » (références non résolues) passent après les vraies
    déclarations analysées.
    """
    nom = (nom or "").strip()
    if not nom:
        return []
    exacts: list[str] = []
    qualnames: list[str] = []
    pointes: list[str] = []
    for noeud in graphe.noeuds():
        nid = noeud.get("id", "")
        if not nid or ":" not in nid:
            continue
        relpath, qualname = nid.rsplit(":", 1)
        if nid == nom:
            exacts.append(nid)
        elif qualname == nom:
            qualnames.append(nid)
        elif f"{_nom_module_pointe(relpath)}.{qualname}" == nom:
            pointes.append(nid)
    def _cle_tri(nid: str) -> tuple:
        # Les nœuds « externe:… » sont synthétiques : ils passent derniers.
        return (nid.startswith("externe:"), nid)
    candidats = (sorted(exacts, key=_cle_tri)
                 + sorted(qualnames, key=_cle_tri)
                 + sorted(pointes, key=_cle_tri))
    return candidats[:limite]


def analyser_impact(nom_mecanisme: str, racine: str | None = None,
                    graphe=None) -> dict:
    """Analyse d'impact d'un symbole sondé. Ne lève jamais d'exception.

    - ``racine`` : dossier Python analysé (défaut : le paquet
      phi_complexity). ``None`` → défaut.
    - ``graphe`` : un ``GrapheDependances`` déjà construit est réutilisé
      tel quel (le CLI le construit une fois par invocation) ; sinon il
      est construit ici.

    Retourne un dict JSON-sérialisable avec les clés :
    ``desactive``, ``racine``, ``symbole_recherche``, ``noeud``,
    ``autres_candidats``, ``graphe`` (résumé), ``dependants``,
    ``risque`` et ``avertissement`` (None en cas de succès).
    """
    nom = (nom_mecanisme or "").strip()
    racine = os.path.abspath(racine) if racine else racine_impact_par_defaut()
    resultat: dict = {
        "desactive": False,
        "racine": racine,
        "symbole_recherche": nom,
        "noeud": None,
        "autres_candidats": [],
        "graphe": {"noeuds": 0, "aretes": 0},
        "dependants": {"nombre": 0, "profondeur_max": 0,
                       "tronque": False, "liste": []},
        "risque": {"score": 0.0, "niveau": "FAIBLE",
                   "facteurs": {}},
        "avertissement": None,
    }
    try:
        if graphe is None:
            from .impact import GrapheDependances
            if not os.path.isdir(racine):
                raise ValueError(f"racine introuvable : {racine}")
            graphe = GrapheDependances.depuis_repertoire(racine)
        noeuds = graphe.noeuds()
        aretes = graphe.vers_json().get("aretes", [])
        resultat["graphe"] = {"noeuds": len(noeuds), "aretes": len(aretes)}
        candidats = trouver_symboles(graphe, nom)
        if not candidats:
            resultat["avertissement"] = (
                f"aucun symbole Python « {nom} » trouvé sous {racine} — "
                "analyse d'impact non applicable à ce mécanisme"
            )
            return resultat
        nid = candidats[0]
        resultat["noeud"] = nid
        resultat["autres_candidats"] = candidats[1:]

        from .impact import impact_avant, score_risque
        impact = impact_avant(graphe, nid)
        liste_ids = list(impact.get("impactes_ids", []))
        resultat["dependants"] = {
            "nombre": int(impact.get("D", 0)),
            "profondeur_max": int(impact.get("P", 0)),
            "tronque": bool(impact.get("tronque", False)),
            "liste": liste_ids[:_LIMITE_LISTE_DEPENDANTS],
        }
        # La couverture de tests se mesure à la racine du dépôt (les tests
        # vivent dans tests/, hors du paquet) : on remonte d'un niveau si
        # la racine analysée est le paquet lui-même.
        racine_couverture = os.path.dirname(racine)
        risque = score_risque(graphe, nid, racine_couverture, impact=impact)
        resultat["risque"] = {
            "score": round(float(risque.get("score", 0.0)), 1),
            "niveau": str(risque.get("niveau", "FAIBLE")),
            "facteurs": dict(risque.get("facteurs", {})),
        }
    except Exception as e:  # dégradation gracieuse : jamais de crash
        resultat["desactive"] = True
        resultat["avertissement"] = (
            f"analyse d'impact indisponible ({type(e).__name__} : {e})"
        )
    return resultat
