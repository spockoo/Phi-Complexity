"""explorateur.py — Explorateur interactif : typographie des symboles + analyse, une seule surface.

Chantier EXPLORATEUR (2026-09-30, validé par Tomy) : fusionner la typographie
des symboles (statuts typés DÉMONTRÉ / CONDITIONNEL / RÉFUTÉ / NON-ATTAQUÉ…)
et l'analyse (sondes A/B, oracle, lentille entropique) en UNE surface
interactive, pour éviter le temps perdu des allers-retours entre outils.

`phi explorer <mécanisme>` génère UN fichier HTML autonome : CSS/JS inline,
JSON embarqué, ZÉRO dépendance réseau/CDN (s'ouvre offline).

Contenu :
  1. graphe SVG des nœuds (route sonde A + terra incognita + inconditionnel
     négatif sonde B), couleur par statut typé, disposition en couches par
     profondeur calculée EN PYTHON (le JS ne fait que rendre) ;
  2. clic sur un nœud → panneau détail (statut, contenu, trous, tag, coût,
     chantiers, fichier:ligne, entropie, double verdict, trace d'oracle) ;
  3. filtres par zone, par bande, recherche texte ;
  4. simulateur « et si on réfutait X ? » : pour chaque nœud CONDITIONNEL,
     recompte EXACT des routes admissibles via `compter_routes_dag`
     (entropie.py) avec le nœud retiré — calculé en Python à la génération,
     le HTML restant 100 % statique/offline ;
  5. en-tête : mécanisme, horodatage, note d'intégrité, disclaimer.

╔══════════════════════════════════════════════════════════════════════╗
║ INTERDICTION FORMELLE (testée mécaniquement)                          ║
║ AUCUN score A/B unique. AUCUNE probabilité P(A). Ni dans le HTML,     ║
║ ni dans le JSON embarqué. Les postérieurs de l'oracle et les bits      ║
║ d'entropie sont des mesures d'ATTENTION de l'instrument, jamais des   ║
║ probabilités de vérité — c'est écrit dans chaque sortie.               ║
╚══════════════════════════════════════════════════════════════════════╝

Conventions durcies (leçons des chantiers précédents) :
- import direct uniquement (sondes, entropie, ancrage, oracle, croyances),
  jamais de subprocess ;
- les objets Hypothese partagés sont COPIÉS avant tout rattachement
  (un instrument qui écrit dans sa source sans le dire ment) ;
- flottants autorisés uniquement sous les sections `entropie` et `oracle`
  (bits d'attention / heuristiques bayésiennes légitimes, jamais des scores) ;
- le modèle en couches du simulateur est le MODÈLE DE RÉFÉRENCE d'entropie.py
  (rangs d'attaque, arêtes biparties complètes) — exhibé comme tel, pas le
  graphe réel des dépendances.

LIMITES (en toutes lettres) : l'explorateur est une CARTE, pas le territoire ;
une liste d'attention, pas une stratégie de preuve ; le veto de Tomy tranche.
Aveugle au contenu des preuves comme tout phi-complexity : seule source, le
registre (+ les croyances pour l'attention) ; registre périmé ⇒ carte périmée.
"""

import copy as _copy
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .core import VERSION
from .entropie import (
    COUT_BOOLEANISATION_ETALON,
    DOSSIER_DEFAUT,
    compter_routes_dag,
    posterieurs_symboles,
)
from .sondes import (
    CLES_INTERDITES,
    INTERDICTION_TEXTE,
    REGISTRE_DEFAUT,
    Hypothese,
    RegistreSondes,
    resoudre_mecanisme,
    sonder,
    zone_sonde,
)
from .sondes import _RANG_TAG
from .ancrage import attacher_entropie, section_entropie_noeud

DISCLAIMER_EXPLORATEUR = (
    "Carte, pas territoire — liste d'attention, pas stratégie de preuve ; "
    "le veto de Tomy tranche."
)

# Sections du JSON embarqué autorisées à contenir des flottants
# (bits d'attention / heuristiques bayésiennes — jamais des scores).
SECTIONS_FLOTTANTS_OK = {"entropie", "oracle", "oracle_dossier"}

# ────────────────────────────────────────────────────────
# COULEURS PAR ZONE TYPÉE (mêmes codes dans le SVG et la légende)
# ────────────────────────────────────────────────────────

COULEURS_ZONES = {
    "DÉMONTRÉ": "#1e7d32",
    "CONDITIONNEL": "#b7791f",
    "RÉFUTÉ": "#b3261e",
    "NON-ATTAQUÉ": "#5f6368",
}
COULEUR_DEFAUT = "#3b5bdb"  # statuts bruts (POINTEUR, PARAMÈTRE…)

BANDES = [
    ("route", "ROUTE VERS L'INCONDITIONNEL — sonde A (chaîne ordonnée)"),
    ("terra_incognita", "TERRA INCOGNITA — nommé, jamais attaqué"),
    ("voies_mortes", "INCONDITIONNEL NÉGATIF — voies mortes, sonde B"),
]

# Géométrie du layout (calculée en Python, le JS ne fait que rendre).
_NOEUD_L, _NOEUD_H = 200, 74
_DX, _DY = 232, 116
_X0 = 28
_COLS_GRILLE = 6


# ────────────────────────────────────────────────────────
# 1. COLLECTE (import direct, lecture seule du registre)
# ────────────────────────────────────────────────────────

def _resoudre_ou_erreur(mecanisme: str, registre: RegistreSondes) -> dict:
    """Résolution du mécanisme, ou ValueError au message propre (jamais de crash)."""
    resolution = resoudre_mecanisme(mecanisme, registre)
    if resolution["type"] == "inconnu" or not resolution.get("trous"):
        raise ValueError(
            f"Mécanisme inconnu : {mecanisme!r} — ni sorry du Master, ni "
            "hypothèse nommée, ni chantier du registre. "
            "Vérifiez l'orthographe (ex. energy_identity, H30, 139) ou "
            "enregistrez le mécanisme au registre avant de l'explorer."
        )
    return resolution


def _traces_oracle_par_nom(dossier: Optional[str], top: int = 60) -> Dict[str, dict]:
    """Traces d'oracle indexées par nom de symbole — best effort.

    Import direct (croyances + oracle), jamais de subprocess. Les noms de
    nœuds du registre (H30, …) correspondent rarement aux symboles Lean :
    l'appariement est exact et conservateur ; sans appariement, le nœud
    n'affiche simplement pas de trace (jamais d'invention).
    """
    if not dossier:
        return {}
    try:
        from . import chemins_croyants
        from .editeur.indexeur import EXCLUSIONS_DEFAUT
        from .oracle import TraceurOracle
        traceur = TraceurOracle()
        chemins_croyants(dossier, exclusions=list(EXCLUSIONS_DEFAUT),
                         top=top, traceur=traceur)
        return {t.symbole: t.vers_dict() for t in traceur.entrees}
    except Exception:
        return {}


def _top_oracle_dossier(traces: Dict[str, dict], n: int = 5) -> List[dict]:
    """Top-n des traces d'oracle du dossier, par postérieur décroissant.

    Contexte d'attention à l'échelle du DOSSIER (pas du nœud) — exhibé comme
    tel dans l'en-tête. Même nature que `phi chemins` : ordonner l'attention,
    jamais un score ni une P(A).
    """
    triees = sorted(traces.values(),
                    key=lambda t: (t.get("posterior") is not None,
                                   t.get("posterior") or 0.0),
                    reverse=True)
    return [
        {"symbole": t.get("symbole"), "posterior": t.get("posterior"),
         "decision": t.get("decision"), "mode": t.get("mode")}
        for t in triees[:n]
    ]


def _copies_avec_entropie(noeuds: List[Hypothese], registre: RegistreSondes,
                          posterieurs: Dict[str, float]) -> List[Hypothese]:
    """Copies (jamais les objets partagés) + section entropie compacte par nœud."""
    copies = []
    for h in noeuds:
        h2 = _copy.copy(h)
        try:
            h2.section_entropie = section_entropie_noeud(h.nom, registre,
                                                         posterieurs)
        except Exception as e:
            h2.section_entropie = {"noeud": h.nom,
                                   "erreur": f"lentille non calculable : {e}"}
        copies.append(h2)
    return copies


# ────────────────────────────────────────────────────────
# 2. GRAPHE (layout en couches calculé en Python)
# ────────────────────────────────────────────────────────

def _noeud_dict(h_dict: dict, nid: str, bande: str, x: int, y: int,
                oracle: Optional[dict]) -> dict:
    """Schéma unique d'un nœud du graphe (JSON-sérialisable)."""
    d = {
        "id": nid,
        "nom": h_dict.get("nom", nid),
        "bande": bande,
        "x": x,
        "y": y,
        "statut": h_dict.get("statut", ""),
        "zone": h_dict.get("zone", ""),
        "trous": list(h_dict.get("trous", [])),
        "contenu": h_dict.get("contenu", ""),
        "tag": h_dict.get("tag", ""),
        "cout_estime": h_dict.get("cout_estime", ""),
        "chantiers": list(h_dict.get("chantiers", [])),
        "fichier_ligne": h_dict.get("fichier_ligne", ""),
        "source": h_dict.get("source", ""),
        "double_statut": (dict(h_dict["double_statut"])
                          if h_dict.get("double_statut") else None),
        "entropie": (dict(h_dict["entropie"])
                     if h_dict.get("entropie") else None),
        "oracle": dict(oracle) if oracle else None,
    }
    # NOTE : aucune clé de CLES_INTERDITES ne doit jamais apparaître ici.
    return d


def construire_graphe(route: List[dict], terra: List[dict],
                      obstructions: List[dict]) -> dict:
    """Nœuds + arêtes + géométrie. Les arêtes honnêtes : la route A est une
    chaîne ordonnée (chaque nœud → le suivant) ; terra incognita et voies
    mortes n'ont PAS d'arêtes connues — aucune n'est inventée."""
    noeuds: List[dict] = []
    aretes: List[dict] = []
    y = 96
    # — Bande route : chaîne horizontale ordonnée —
    for i, h in enumerate(route):
        nid = f"R{i}"
        noeuds.append({"_h": h, "_id": nid, "_bande": "route",
                       "_x": _X0 + i * _DX, "_y": y})
        if i > 0:
            aretes.append({"de": f"R{i-1}", "vers": nid})
    y_route_bas = y + _NOEUD_H
    # — Bande terra : grille —
    y = y_route_bas + 96
    for i, h in enumerate(terra):
        nid = f"T{i}"
        noeuds.append({"_h": h, "_id": nid, "_bande": "terra_incognita",
                       "_x": _X0 + (i % _COLS_GRILLE) * _DX,
                       "_y": y + (i // _COLS_GRILLE) * _DY})
    y_terra_bas = y + ((max(len(terra), 1) - 1) // _COLS_GRILLE) * _DY + _NOEUD_H
    # — Bande voies mortes : grille —
    y = y_terra_bas + 96
    for i, o in enumerate(obstructions):
        nid = f"B{i}"
        noeuds.append({"_h": o, "_id": nid, "_bande": "voies_mortes",
                       "_x": _X0 + (i % _COLS_GRILLE) * _DX,
                       "_y": y + (i // _COLS_GRILLE) * _DY})
    y_mortes_bas = y + ((max(len(obstructions), 1) - 1) // _COLS_GRILLE) * _DY + _NOEUD_H

    largeur = max(
        _X0 + max(len(route), 1) * _DX,
        _X0 + _COLS_GRILLE * _DX,
    ) + 40
    bandes_geo = [
        {"cle": "route", "titre": BANDES[0][1], "y": 96 - 34},
        {"cle": "terra_incognita", "titre": BANDES[1][1],
         "y": y_route_bas + 96 - 34},
        {"cle": "voies_mortes", "titre": BANDES[2][1],
         "y": y_terra_bas + 96 - 34},
    ]
    return {
        "noeuds_bruts": noeuds,  # résolu en nœuds finaux par l'appelant
        "aretes": aretes,
        "largeur": largeur,
        "hauteur": y_mortes_bas + 48,
        "bandes": bandes_geo,
    }


# ────────────────────────────────────────────────────────
# 3. SIMULATEUR « et si on réfutait X ? »
# ────────────────────────────────────────────────────────

def construire_simulateur(noeuds: List[dict]) -> dict:
    """Recompte EXACT des routes admissibles (compter_routes_dag) avec chaque
    nœud CONDITIONNEL retiré — calculé ici en Python, servi statique au HTML.

    Modèle : couches = rangs d'attaque du registre, arêtes biparties complètes
    entre couches consécutives, nœuds RÉFUTÉ retirés — le MODÈLE DE RÉFÉRENCE
    d'entropie.py (dag_couches_attaque), exhibé comme tel, pas le graphe réel.
    """
    # Dédupliquer par nom (un nœud peut figurer route + terra).
    vus, uniques = set(), []
    for n in noeuds:
        if n["nom"] not in vus:
            vus.add(n["nom"])
            uniques.append(n)
    couches: Dict[int, List[str]] = {}
    for n in uniques:
        if n.get("zone") == "RÉFUTÉ":
            continue  # transition interdite : nœud retiré
        r = _RANG_TAG.get(n.get("tag", ""), 4)
        couches.setdefault(r, []).append(n["nom"])
    rangs = sorted(couches)
    preds: Dict[str, List[str]] = {}
    for i, r in enumerate(rangs):
        for nom in sorted(couches[r]):
            preds[nom] = sorted(couches[rangs[i - 1]]) if i > 0 else []
    resultat = {
        "modele": ("couches = rangs d'attaque (portes→murs→forteresses→"
                   "hors-classe→non tagué) ; arêtes biparties complètes ; "
                   "nœuds RÉFUTÉ retirés — MODÈLE DE RÉFÉRENCE (entropie.py), "
                   "pas le graphe réel des dépendances"),
        "hypotheses": [],
    }
    try:
        avant = compter_routes_dag(preds, interdits=set())
    except ValueError as e:
        resultat["erreur"] = (f"cycle détecté dans le modèle en couches : {e} "
                              "— aucun comptage (résultat faux interdit)")
        return resultat
    resultat["avant"] = {
        "nb_routes": avant["nb_routes"],
        "nb_couches": len(rangs),
        "tailles_couches": [len(couches[r]) for r in rangs],
    }
    for n in uniques:
        if n.get("zone") != "CONDITIONNEL":
            continue
        try:
            apres = compter_routes_dag(preds, interdits={n["nom"]})
            resultat["hypotheses"].append({
                "nom": n["nom"],
                "statut": n.get("statut", ""),
                "routes_apres": apres["nb_routes"],
                "delta": apres["nb_routes"] - avant["nb_routes"],
            })
        except ValueError as e:
            resultat["hypotheses"].append({
                "nom": n["nom"],
                "statut": n.get("statut", ""),
                "erreur": f"cycle après retrait : {e}",
            })
    return resultat


# ────────────────────────────────────────────────────────
# 4. DONNÉES COMPLÈTES
# ────────────────────────────────────────────────────────

def _entropie_mecanisme_compacte(plein: dict) -> dict:
    """Sous-ensemble instrumental de la section entropie du mécanisme."""
    bc = plein.get("booleanization_cost") or {}
    rt = plein.get("routes_admissibles") or {}
    return {
        "h_initial_bits": plein.get("h_initial_bits"),
        "h_finale_bits": plein.get("h_finale_bits"),
        "delta_h_total_bits": ((plein.get("h_initial_bits") or 0.0)
                               - (plein.get("h_finale_bits") or 0.0)),
        "booleanization_cost_bits": bc.get("booleanization_cost_bits"),
        "ratio_etalons_or": bc.get("ratio_vs_etalon"),
        "nb_routes_admissibles": rt.get("nb_routes_admissibles"),
        "etalon_or_bits": COUT_BOOLEANISATION_ETALON,
        "note": ("Bits d'attention de l'instrument — jamais une probabilité. "
                 "Étalon-or : 1−H_φ ≈ 0.0405812718 bits."),
    }


def construire_donnees_explorateur(
    mecanisme: str,
    registre: Optional[RegistreSondes] = None,
    dossier: Optional[str] = DOSSIER_DEFAUT,
    posterieurs: Optional[Dict[str, float]] = None,
    avec_oracle: bool = True,
) -> dict:
    """Construit le dict embarqué dans le HTML. Lecture seule du registre.

    - dossier=None : pas d'indexation des croyances (rapide ; postérieurs
      uniformes exhibés, pas de traces d'oracle).
    - Ne mute JAMAIS les objets Hypothese du registre (copies systématiques).
    """
    if registre is None:
        registre = RegistreSondes().charger(REGISTRE_DEFAUT)
    _resoudre_ou_erreur(mecanisme, registre)

    if posterieurs is None:
        posterieurs = posterieurs_symboles(dossier) if dossier else {}

    resultat = sonder(mecanisme, registre)
    resultat = attacher_entropie(resultat, registre, posterieurs)
    terra_copies = _copies_avec_entropie(list(resultat.terra_incognita),
                                         registre, posterieurs)

    traces = _traces_oracle_par_nom(dossier) if (avec_oracle and dossier) else {}
    oracle_dossier = _top_oracle_dossier(traces) if traces else []

    route_h = [h.vers_dict() for h in resultat.route_a]
    terra_h = [h.vers_dict() for h in terra_copies]
    # Obstructions sonde B → nœuds « voies mortes » (zone RÉFUTÉ).
    obs_h = []
    for o in resultat.obstructions_b:
        obs_h.append({
            "nom": o.titre,
            "statut": "/".join(o.verdicts) if o.verdicts else "RÉFUTÉ",
            "zone": "RÉFUTÉ",
            "trous": list(o.trous),
            "contenu": o.vice or o.titre,
            "tag": "voie morte (sonde B)",
            "cout_estime": "",
            "chantiers": [o.chantier] if o.chantier else [],
            "fichier_ligne": "",
            "source": o.ancrage or "registre §5",
            "interdit": o.interdit,
            "remplace": o.remplace,
            "double_statut": None,
            "entropie": None,
        })

    graphe = construire_graphe(route_h, terra_h, obs_h)
    noeuds_finaux = []
    for brut in graphe["noeuds_bruts"]:
        nd = _noeud_dict(brut["_h"], brut["_id"], brut["_bande"],
                         brut["_x"], brut["_y"],
                         traces.get(brut["_h"].get("nom", "")))
        # Champs spécifiques aux obstructions (voies mortes).
        for cle in ("interdit", "remplace"):
            if cle in brut["_h"]:
                nd[cle] = brut["_h"][cle]
        noeuds_finaux.append(nd)

    simulateur = construire_simulateur(noeuds_finaux)

    donnees = {
        "mecanisme": mecanisme,
        "type_mecanisme": resultat.type_mecanisme,
        "statut_mecanisme": resultat.statut_mecanisme,
        "trous": list(resultat.trous),
        "doubles_verdicts": [dict(dv) for dv in resultat.doubles_verdicts],
        "entropie": _entropie_mecanisme_compacte(resultat.entropie or {}),
        "graphe": {
            "noeuds": noeuds_finaux,
            "aretes": graphe["aretes"],
            "largeur": graphe["largeur"],
            "hauteur": graphe["hauteur"],
            "bandes": graphe["bandes"],
        },
        "simulateur": simulateur,
        "notes": list(resultat.notes),
        "integrite": dict(resultat.integrite),
        "disclaimer": DISCLAIMER_EXPLORATEUR,
        "interdiction": INTERDICTION_TEXTE,
        "oracle_dossier": oracle_dossier,
        "couleurs_zones": dict(COULEURS_ZONES),
        "couleur_defaut": COULEUR_DEFAUT,
        "limites": (
            "EXPLORATEUR — carte, pas territoire : le graphe montre la route "            "exhibée par la sonde, pas sa praticabilité ; la terra incognita "
            "montre où l'on n'est pas allé, pas qu'il y a un passage ; le "
            "simulateur rejoue le MODÈLE DE RÉFÉRENCE en couches, pas le graphe "
            "réel. Aveugle au contenu des preuves : seule source, le registre "
            "(+ croyances pour l'attention) ; registre périmé ⇒ carte périmée. "
            + INTERDICTION_TEXTE
        ),
        "version_phi": VERSION,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }
    # NOTE : aucune clé de CLES_INTERDITES ne doit jamais apparaître ici
    # (testé mécaniquement dans tests/test_explorateur.py).
    return donnees


def donnees_vers_json_embarquable(donnees: dict) -> str:
    """Sérialise pour la balise <script type="application/json">.

    Échappe `</` pour qu'aucun contenu du registre ne puisse fermer la balise.
    """
    texte = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"))
    return texte.replace("</", "<\\/")


# ────────────────────────────────────────────────────────
# 5. GABARIT HTML (autonome : CSS/JS inline, zéro réseau)
# ────────────────────────────────────────────────────────

GABARIT_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITRE__</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; padding: 0 18px 40px 18px; color: #1a1a1a; background: #fafafa; }
  header.explo { border-bottom: 3px solid #1a1a1a; padding: 14px 0 10px 0; }
  header.explo h1 { margin: 0 0 4px 0; font-size: 1.35rem; }
  header.explo .meta { font-size: 0.85rem; color: #444; }
  .disclaimer { background: #fff8e1; border: 1px solid #e0a800; border-radius: 6px;
                padding: 8px 12px; margin: 12px 0; font-size: 0.9rem; }
  #controles { display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
               background: #fff; border: 1px solid #ddd; border-radius: 6px;
               padding: 10px 14px; margin: 12px 0; font-size: 0.88rem; }
  #controles fieldset { border: 1px solid #ccc; border-radius: 4px; padding: 4px 10px; }
  #controles legend { font-size: 0.78rem; color: #555; padding: 0 4px; }
  #controles label { margin-right: 10px; white-space: nowrap; }
  #controles input[type="text"] { padding: 4px 8px; min-width: 220px; }
  .pastille { display: inline-block; width: 12px; height: 12px; border-radius: 3px;
              margin-right: 4px; vertical-align: -1px; }
  main.explo { display: flex; gap: 14px; align-items: flex-start; }
  #zone-graphe { flex: 1 1 auto; min-width: 0; overflow: auto;
                 background: #fff; border: 1px solid #ddd; border-radius: 6px; }
  #graphe { display: block; }
  #panneau { flex: 0 0 360px; background: #fff; border: 1px solid #ddd;
             border-radius: 6px; padding: 12px 14px; font-size: 0.86rem;
             position: sticky; top: 10px; max-height: 85vh; overflow: auto; }
  #panneau h2 { margin: 0 0 6px 0; font-size: 1.02rem; word-break: break-word; }
  #panneau .vide { color: #777; font-style: italic; }
  #panneau dl { margin: 8px 0; }
  #panneau dt { font-weight: 600; margin-top: 8px; font-size: 0.8rem;
                text-transform: uppercase; letter-spacing: 0.04em; color: #555; }
  #panneau dd { margin: 2px 0 0 0; word-break: break-word; }
  #panneau .bloc { background: #f6f6f6; border-left: 3px solid #999;
                   padding: 6px 10px; margin-top: 8px; border-radius: 0 4px 4px 0; }
  #panneau button.simuler { margin-top: 10px; padding: 7px 12px; cursor: pointer;
                   background: #b3261e; color: #fff; border: none; border-radius: 4px;
                   font-size: 0.86rem; }
  #panneau button.simuler:hover { background: #8f1d17; }
  .noeud { cursor: pointer; }
  .noeud rect.corps { stroke-width: 2; }
  .noeud:hover rect.corps { stroke-width: 3.5; }
  .noeud.selectionne rect.corps { stroke: #111; stroke-width: 3.5; }
  section#simulateur { background: #fff; border: 1px solid #ddd; border-radius: 6px;
                       padding: 12px 16px; margin-top: 16px; }
  section#simulateur h2 { margin-top: 0; }
  section#simulateur table { border-collapse: collapse; width: 100%; font-size: 0.86rem; }
  section#simulateur th, section#simulateur td { border: 1px solid #ddd;
                       padding: 6px 10px; text-align: left; }
  section#simulateur th { background: #f0f0f0; }
  section#simulateur tr.flash td { background: #ffe9e7; }
  .modele-note { font-size: 0.82rem; color: #555; background: #f6f6f6;
                 border-radius: 4px; padding: 8px 12px; margin: 10px 0; }
  footer.explo { margin-top: 18px; font-size: 0.78rem; color: #666;
                 border-top: 1px solid #ccc; padding-top: 10px; }
</style>
</head>
<body>
<header class="explo" id="entete"></header>
<div class="disclaimer" id="disclaimer"></div>
<div id="controles">
  <fieldset id="filtre-zones"><legend>Zones (statuts typés)</legend></fieldset>
  <label>Bande :
    <select id="filtre-bande">
      <option value="">toutes</option>
      <option value="route">route (sonde A)</option>
      <option value="terra_incognita">terra incognita</option>
      <option value="voies_mortes">voies mortes (sonde B)</option>
    </select>
  </label>
  <label>Recherche : <input type="text" id="recherche" placeholder="nom ou contenu…"></label>
  <span id="compte"></span>
</div>
<main class="explo">
  <div id="zone-graphe"><svg id="graphe" role="img" aria-label="Graphe du mécanisme"></svg></div>
  <aside id="panneau"><p class="vide">Cliquez sur un nœud du graphe pour voir le détail : statut typé, trace d'oracle, entropie, simulateur.</p></aside>
</main>
<section id="simulateur"></section>
<footer class="explo" id="pied"></footer>
<script type="application/json" id="phi-donnees">__DONNEES_JSON__</script>
<script>
'use strict';
var DATA = JSON.parse(document.getElementById('phi-donnees').textContent);
var SVGNS = 'http://www.w3.org/2000/svg';
var COULEURS = DATA.couleurs_zones || {};
var COULEUR_DEFAUT = DATA.couleur_defaut || '#3b5bdb';
var NL = 200, NH = 74;

function couleur(zone) { return COULEURS[zone] || COULEUR_DEFAUT; }
function el(tag, attrs, parent) {
  var e = document.createElementNS(SVGNS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function tx(parent, x, y, s, attrs) {
  var t = el('text', Object.assign({x: x, y: y}, attrs || {}), parent);
  t.textContent = (s === null || s === undefined) ? '' : String(s);
  return t;
}
function tronquer(s, n) {
  s = (s === null || s === undefined) ? '' : String(s);
  return s.length > n ? s.slice(0, n - 1) + '\u2026' : s;
}
function fmt(x, dec) {
  if (x === null || x === undefined) return 'n/a';
  return Number(x).toFixed(dec === undefined ? 4 : dec);
}
var PAR_ID = {};
function bandeTitre(cle) {
  var b = (DATA.graphe.bandes || []).filter(function(x){ return x.cle === cle; })[0];
  return b ? b.titre : cle;
}

function dessiner() {
  var svg = document.getElementById('graphe');
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('width', DATA.graphe.largeur);
  svg.setAttribute('height', DATA.graphe.hauteur);
  var defs = el('defs', {}, svg);
  var mk = el('marker', {id: 'fleche', viewBox: '0 0 10 10', refX: 9, refY: 5,
    markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse'}, defs);
  el('path', {d: 'M 0 1 L 9 5 L 0 9 z', fill: '#999'}, mk);
  (DATA.graphe.bandes || []).forEach(function(b) {
    tx(svg, 28, b.y, b.titre, {'font-size': '13', 'font-weight': 'bold', fill: '#333'});
  });
  (DATA.graphe.aretes || []).forEach(function(a) {
    var n1 = PAR_ID[a.de], n2 = PAR_ID[a.vers];
    if (!n1 || !n2) return;
    el('line', {x1: n1.x + NL/2, y1: n1.y + NH, x2: n2.x + NL/2, y2: n2.y,
      stroke: '#999', 'stroke-width': 1.6, 'marker-end': 'url(#fleche)',
      'class': 'arete', 'data-de': a.de, 'data-vers': a.vers}, svg);
  });
  PAR_ID = {};
  (DATA.graphe.noeuds || []).forEach(function(n) {
    PAR_ID[n.id] = n;
    var g = el('g', {'class': 'noeud', 'data-id': n.id, 'data-zone': n.zone,
      'data-bande': n.bande}, svg);
    el('rect', {x: n.x, y: n.y, width: NL, height: NH, rx: 8,
      'class': 'corps', fill: '#ffffff', stroke: couleur(n.zone)}, g);
    el('rect', {x: n.x, y: n.y, width: NL, height: 10, rx: 5, fill: couleur(n.zone)}, g);
    tx(g, n.x + 10, n.y + 30, tronquer(n.nom, 26),
      {'font-size': '12.5', 'font-weight': 'bold', fill: '#111'});
    tx(g, n.x + 10, n.y + 48, tronquer(n.zone || n.statut, 26),
      {'font-size': '11.5', fill: '#444'});
    tx(g, n.x + 10, n.y + 64, tronquer(n.tag || '', 30),
      {'font-size': '10.5', fill: '#777', 'font-style': 'italic'});
    g.addEventListener('click', function() { afficherDetail(n.id); });
  });
  appliquerFiltres();
}

function zonesPresentes() {
  var z = {};
  (DATA.graphe.noeuds || []).forEach(function(n){ z[n.zone || '?'] = true; });
  return Object.keys(z).sort();
}

function construireControles() {
  var fz = document.getElementById('filtre-zones');
  zonesPresentes().forEach(function(z) {
    var lab = document.createElement('label');
    var cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = true; cb.value = z;
    cb.addEventListener('change', appliquerFiltres);
    lab.appendChild(cb);
    var p = document.createElement('span');
    p.className = 'pastille'; p.style.background = couleur(z);
    lab.appendChild(p);
    lab.appendChild(document.createTextNode(z));
    fz.appendChild(lab);
  });
  document.getElementById('filtre-bande').addEventListener('change', appliquerFiltres);
  document.getElementById('recherche').addEventListener('input', appliquerFiltres);
}

function appliquerFiltres() {
  var zones = {};
  document.querySelectorAll('#filtre-zones input[type=checkbox]').forEach(function(cb){
    zones[cb.value] = cb.checked;
  });
  var bande = document.getElementById('filtre-bande').value;
  var q = document.getElementById('recherche').value.toLowerCase();
  var visibles = 0, total = 0;
  document.querySelectorAll('#graphe .noeud').forEach(function(g) {
    total++;
    var n = PAR_ID[g.getAttribute('data-id')];
    var ok = zones[n.zone] !== false
      && (!bande || n.bande === bande)
      && (!q || (n.nom + ' ' + (n.contenu || '')).toLowerCase().indexOf(q) >= 0);
    g.style.display = ok ? '' : 'none';
    if (ok) visibles++;
  });
  document.querySelectorAll('#graphe .arete').forEach(function(a) {
    var d1 = document.querySelector('#graphe .noeud[data-id="' + a.getAttribute('data-de') + '"]');
    var d2 = document.querySelector('#graphe .noeud[data-id="' + a.getAttribute('data-vers') + '"]');
    a.style.display = (d1 && d2 && d1.style.display !== 'none' && d2.style.display !== 'none') ? '' : 'none';
  });
  document.getElementById('compte').textContent = visibles + ' / ' + total + ' nœuds visibles';
}

function ligne(dl, titre, valeur) {
  if (valeur === null || valeur === undefined || valeur === '') return;
  var dt = document.createElement('dt'); dt.textContent = titre;
  var dd = document.createElement('dd'); dd.textContent = valeur;
  dl.appendChild(dt); dl.appendChild(dd);
}

function afficherDetail(id) {
  var n = PAR_ID[id];
  if (!n) return;
  document.querySelectorAll('#graphe .noeud').forEach(function(g){
    g.classList.toggle('selectionne', g.getAttribute('data-id') === id);
  });
  var p = document.getElementById('panneau');
  while (p.firstChild) p.removeChild(p.firstChild);
  var h2 = document.createElement('h2'); h2.textContent = n.nom; p.appendChild(h2);
  var bande = document.createElement('div');
  bande.style.cssText = 'font-size:0.78rem;color:#555;margin-bottom:6px;';
  bande.textContent = bandeTitre(n.bande);
  p.appendChild(bande);
  var dl = document.createElement('dl'); p.appendChild(dl);
  ligne(dl, 'Statut typé', n.statut + (n.zone && n.zone !== n.statut ? '  → zone ' + n.zone : ''));
  ligne(dl, 'Contenu', n.contenu);
  if (n.trous && n.trous.length) ligne(dl, 'Trous', n.trous.join(', '));
  ligne(dl, 'Tag', n.tag);
  ligne(dl, 'Coût estimé', n.cout_estime);
  if (n.chantiers && n.chantiers.length) ligne(dl, 'Chantiers', n.chantiers.join(', '));
  ligne(dl, 'Fichier:ligne', n.fichier_ligne);
  ligne(dl, 'Source', n.source);
  if (n.interdit) ligne(dl, 'Interdit', n.interdit);
  if (n.remplace) ligne(dl, 'Voie de remplacement', n.remplace);
  if (n.double_statut) {
    var bdv = document.createElement('div'); bdv.className = 'bloc';
    var tdv = document.createElement('strong'); tdv.textContent = 'Double verdict'; bdv.appendChild(tdv);
    Object.keys(n.double_statut).forEach(function(k){
      var d = document.createElement('div'); d.textContent = k + ' : ' + n.double_statut[k];
      bdv.appendChild(d);
    });
    p.appendChild(bdv);
  }
  if (n.entropie) {
    var be = document.createElement('div'); be.className = 'bloc';
    var te = document.createElement('strong'); te.textContent = 'Entropie (bits d\u2019attention)'; be.appendChild(te);
    var e = n.entropie, bc = e.booleanization_cost || {};
    [['H initiale', fmt(e.h_initial_bits) + ' bits'],
     ['ΔH totale', fmt(e.delta_h_total_bits) + ' bits'],
     ['Coût de booléanisation', fmt(bc.booleanization_cost_bits) + ' bits (' + fmt(bc.ratio_vs_etalon, 2) + ' étalons-or)'],
     ['Routes admissibles (modèle)', e.routes_admissibles ? String(e.routes_admissibles.nb_routes_admissibles) : 'n/a']
    ].forEach(function(paire){
      var d = document.createElement('div'); d.textContent = paire[0] + ' : ' + paire[1];
      be.appendChild(d);
    });
    p.appendChild(be);
  }
  if (n.oracle) {
    var bo = document.createElement('div'); bo.className = 'bloc';
    var to = document.createElement('strong'); to.textContent = 'Trace d\u2019oracle (heuristique, pas une vérité)'; bo.appendChild(to);
    var o = n.oracle;
    var termes = Object.keys(o.termes_evidence || {}).map(function(k){
      return k + '=' + fmt(o.termes_evidence[k]);
    }).join('  ');
    [['Prior', fmt(o.prior)], ['Évidences', termes],
     ['Postérieur', fmt(o.posterior)],
     ['Borne d\u2019erreur', o.borne_erreur_certifiee === null ? 'non certifiée (mode flottant)' : String(o.borne_erreur_certifiee)],
     ['Décision soutenue', o.decision], ['Mode', o.mode]
    ].forEach(function(paire){
      var d = document.createElement('div'); d.textContent = paire[0] + ' : ' + paire[1];
      bo.appendChild(d);
    });
    p.appendChild(bo);
  } else {
    var bno = document.createElement('div');
    bno.style.cssText = 'font-size:0.8rem;color:#777;margin-top:8px;font-style:italic;';
    bno.textContent = 'Aucune trace d\u2019oracle appariée à ce nœud ' +
      '(appariement exact nom ↔ symbole Lean ; les deux nomenclatures sont ' +
      'disjointes à ce jour — la trace s\u2019affichera dès qu\u2019un nom coïncidera).';
    p.appendChild(bno);
  }
  if (n.zone === 'CONDITIONNEL') {
    var btn = document.createElement('button');
    btn.className = 'simuler';
    btn.textContent = 'Simuler la réfutation de \u00ab ' + tronquer(n.nom, 30) + ' \u00bb';
    btn.addEventListener('click', function() { simulerRefutation(n.nom); });
    p.appendChild(btn);
  }
}

function construireSimulateur() {
  var sec = document.getElementById('simulateur');
  var h2 = document.createElement('h2');
  h2.textContent = 'Simulateur \u2014 \u00ab et si on r\u00e9futait X ? \u00bb';
  sec.appendChild(h2);
  var s = DATA.simulateur || {};
  var note = document.createElement('div'); note.className = 'modele-note';
  note.textContent = s.modele || '';
  sec.appendChild(note);
  if (s.erreur) {
    var pe = document.createElement('p'); pe.textContent = s.erreur; sec.appendChild(pe);
    return;
  }
  var av = s.avant || {};
  var pav = document.createElement('p');
  pav.textContent = 'Routes admissibles avant réfutation : ' + av.nb_routes +
    '  (' + av.nb_couches + ' couches, tailles ' + (av.tailles_couches || []).join('-') + ').';
  sec.appendChild(pav);
  var table = document.createElement('table');
  var thead = document.createElement('thead');
  var trh = document.createElement('tr');
  ['Hypothèse CONDITIONNELLE', 'Routes après réfutation', 'Δ (après − avant)'].forEach(function(t){
    var th = document.createElement('th'); th.textContent = t; trh.appendChild(th);
  });
  thead.appendChild(trh); table.appendChild(thead);
  var tbody = document.createElement('tbody'); tbody.id = 'corps-simulateur';
  (s.hypotheses || []).forEach(function(h) {
    var tr = document.createElement('tr');
    tr.id = 'sim-' + h.nom.replace(/[^A-Za-z0-9_]/g, '_');
    var td1 = document.createElement('td'); td1.textContent = h.nom + '  [' + h.statut + ']';
    var td2 = document.createElement('td');
    td2.textContent = h.erreur ? h.erreur : String(h.routes_apres);
    var td3 = document.createElement('td');
    td3.textContent = h.erreur ? '—' : (h.delta >= 0 ? '+' : '') + h.delta;
    tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody); sec.appendChild(table);
  var plec = document.createElement('p');
  plec.style.cssText = 'font-size:0.82rem;color:#555;';
  plec.textContent = 'Lecture : Δ = (routes après) − (routes avant) dans le MODÈLE ' +
    'EN COUCHES de référence. Δ<0 = la réfutation retire des routes (élagage) ; ' +
    'Δ=0 = le modèle recompense par les couches restantes — ex. retirer le seul ' +
    'nœud d\u2019une couche promeut la couche suivante au rang de source. ' +
    'Comptage exact (entiers arbitraires) — jamais un score, jamais une probabilité.';
  sec.appendChild(plec);
}

function simulerRefutation(nom) {
  document.getElementById('simulateur').scrollIntoView({behavior: 'smooth'});
  var tr = document.getElementById('sim-' + nom.replace(/[^A-Za-z0-9_]/g, '_'));
  if (tr) {
    tr.classList.add('flash');
    setTimeout(function(){ tr.classList.remove('flash'); }, 2600);
  }
}

function construireEntete() {
  var ent = document.getElementById('entete');
  var h1 = document.createElement('h1');
  h1.textContent = 'Explorateur \u2014 ' + DATA.mecanisme;
  ent.appendChild(h1);
  var meta = document.createElement('div'); meta.className = 'meta';
  meta.textContent = '[' + DATA.type_mecanisme + '] ' + (DATA.statut_mecanisme || '') +
    '   ·   trous ' + (DATA.trous || []).join(', ') +
    '   ·   ' + DATA.horodatage + '   ·   phi-complexity ' + DATA.version_phi;
  ent.appendChild(meta);
  if ((DATA.doubles_verdicts || []).length) {
    var dv = document.createElement('div'); dv.className = 'meta';
    dv.textContent = 'Double verdict : ' +
      DATA.doubles_verdicts.map(function(x){ return JSON.stringify(x); }).join(' ; ');
    ent.appendChild(dv);
  }
  var em = DATA.entropie || {};
  var me = document.createElement('div'); me.className = 'meta';
  me.textContent = 'Lentille mécanisme : H=' + fmt(em.h_initial_bits) + ' bits, ΔH=' +
    fmt(em.delta_h_total_bits) + ' bits, coût booléen=' + fmt(em.booleanization_cost_bits) +
    ' bits (' + fmt(em.ratio_etalons_or, 2) + ' étalons-or), routes=' + em.nb_routes_admissibles + '.';
  ent.appendChild(me);
  document.getElementById('disclaimer').textContent = DATA.disclaimer || '';
  var od = DATA.oracle_dossier || [];
  if (od.length) {
    var det = document.createElement('details');
    det.style.cssText = 'font-size:0.82rem;color:#444;margin:6px 0;';
    var sum = document.createElement('summary');
    sum.textContent = 'Attention bayésienne du dossier — top ' + od.length +
      ' symboles par postérieur (contexte dossier, pas par nœud)';
    det.appendChild(sum);
    od.forEach(function(t){
      var d = document.createElement('div');
      d.textContent = t.symbole + ' — postérieur ' + fmt(t.posterior) +
        ' — ' + t.decision + ' [' + t.mode + ']';
      det.appendChild(d);
    });
    var nd = document.createElement('div');
    nd.style.fontStyle = 'italic';
    nd.textContent = 'Heuristique d\u2019attention de l\u2019instrument — jamais une probabilité de vérité.';
    det.appendChild(nd);
    ent.appendChild(det);
  }
  var pied = document.getElementById('pied');
  var pl = document.createElement('div'); pl.textContent = DATA.limites || ''; pied.appendChild(pl);
  var pi = document.createElement('div'); pi.textContent = DATA.interdiction || ''; pied.appendChild(pi);
  var integ = DATA.integrite || {};
  if (Object.keys(integ).length) {
    var pg = document.createElement('div');
    pg.textContent = 'Intégrité : ' + Object.keys(integ).map(function(k){ return k + '=' + integ[k]; }).join(', ');
    pied.appendChild(pg);
  }
}

construireEntete();
construireControles();
dessiner();
construireSimulateur();
</script>
</body>
</html>
"""


# ────────────────────────────────────────────────────────
# 6. GÉNÉRATION
# ────────────────────────────────────────────────────────

def generer_html(donnees: dict) -> str:
    """Remplit le gabarit : titre + JSON embarqué. Zéro dépendance réseau."""
    titre = f"Explorateur phi — {donnees.get('mecanisme', '?')}"
    page = GABARIT_HTML.replace("__TITRE__", titre)
    page = page.replace("__DONNEES_JSON__",
                        donnees_vers_json_embarquable(donnees))
    return page


def _nom_fichier_sortie(mecanisme: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_\-]", "_", mecanisme.strip()) or "mecanisme"
    return f"explorateur_{base}.html"


def explorer_vers_fichier(
    mecanisme: str,
    dossier: Optional[str] = DOSSIER_DEFAUT,
    sortie: Optional[str] = None,
    registre: Optional[RegistreSondes] = None,
    avec_oracle: bool = True,
) -> str:
    """Point d'entrée : construit les données, génère le HTML, l'écrit.

    Rend le chemin du fichier écrit. Mécanisme inconnu → ValueError au
    message propre (le CLI la convertit en sortie ❌ + code 1).
    """
    donnees = construire_donnees_explorateur(
        mecanisme, registre=registre, dossier=dossier, avec_oracle=avec_oracle)
    if sortie is None:
        sortie = _nom_fichier_sortie(mecanisme)
    with open(sortie, "w", encoding="utf-8") as f:
        f.write(generer_html(donnees))
    return sortie
