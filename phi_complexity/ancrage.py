"""ancrage.py — Ancrage de la lentille entropique dans la chaîne des sondes.

Chantier ANCRAGE-ENTROPIE (2026-09-30, Tomy : « Ancrons-la dans la chaîne,
le code nous dira où aller »).

Ce module coud la lentille (entropie.py) à la chaîne des sondes (sondes.py),
par IMPORT DIRECT — jamais de subprocess :

1. `attacher_entropie(resultat_sonde, registre, posterieurs)` : chaque nœud
   du DAG reçoit sa section `entropie` (h_initiale, trace ΔH, h_finale,
   booleanization_cost en bits + ratio en étalons-or, routes_admissibles) ;
   le mécanisme reçoit la section complète.
2. `construire_ou_aller(registre, posterieurs)` : mesures typées par
   mécanisme (6 sorrys du Master + hypothèses §1), TRIÉES par coût de
   booléanisation décroissant — une LISTE D'ATTENTION ORDONNÉE.
3. `rendre_ou_aller_console(data)` : rendu lisible de cette liste.

╔══════════════════════════════════════════════════════════════════════╗
║ INTERDICTION FORMELLE (testée mécaniquement)                          ║
║ AUCUN score A/B unique. AUCUNE probabilité P(A). Le tri par coût de   ║
║ booléanisation n'est PAS un score : il ordonne où le typage porte le  ║
║ plus de bits — c'est-à-dire où l'effondrement en booléen détruirait  ║
║ le plus d'information, donc où regarder en premier. L'instrument      ║
║ montre où regarder ; il ne décide pas. Le veto de Tomy tranche.       ║
╚══════════════════════════════════════════════════════════════════════╝

Non-garanties (voir ANCRAGE_ENTROPIE_20260930.md) : les étalons structurent
la mesure de l'instrument, ils ne prouvent rien sur les mathématiques ; le
modèle en couches des routes est un modèle de référence, pas le graphe réel.
"""

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .core import VERSION
from .entropie import (
    COUT_BOOLEANISATION_ETALON,
    entropie_depuis_sonde,
)
from .sondes import (
    CLES_INTERDITES,
    INTERDICTION_TEXTE,
    SORRY_VERS_TROU,
    Hypothese,
    RegistreSondes,
    ResultatSonde,
    sonder,
)

# Registre et dossier par défaut : mêmes sources que les sondes / l'entropie.
from .sondes import REGISTRE_DEFAUT
from .entropie import DOSSIER_DEFAUT

LIMITE_ANCRAGE = (
    "ANCRAGE ENTROPIE — limites : les sections `entropie` mesurent le "
    "resserrement de l'ATTENTION DE L'INSTRUMENT (bits), jamais la probabilité "
    "que (A) soit vrai. Le tri de `phi ou-aller` ordonne où le typage porte le "
    "plus de bits — où regarder en premier — ce n'est ni un score ni une "
    "probabilité. Le modèle en couches des routes est un modèle de référence, "
    "pas le graphe réel des dépendances. " + INTERDICTION_TEXTE
)


# ────────────────────────────────────────────────────────
# 1. SECTIONS ENTROPIQUES PAR NŒUD (import direct, jamais subprocess)
# ────────────────────────────────────────────────────────

def section_entropie_noeud(nom_noeud: str, registre: RegistreSondes,
                           posterieurs: Dict[str, float]) -> dict:
    """Section `entropie` compacte d'un nœud : sa propre lentille.

    Calcule via entropie_depuis_sonde (import direct) avec registre et
    postérieurs partagés, puis ne garde que les champs instrumentaux.
    Ne crashe jamais : nœud non résolu → section vide documentée.
    """
    try:
        res = entropie_depuis_sonde(nom_noeud, registre=registre,
                                    posterieurs=posterieurs)
    except Exception as e:
        return {"erreur": f"lentille non calculable : {e}",
                "noeud": nom_noeud}
    d = res.vers_dict()
    if not d.get("partition"):
        return {"noeud": nom_noeud,
                "note": " ; ".join(d.get("notes", [])) or "partition vide"}
    bc = d.get("booleanization_cost") or {}
    rt = d.get("routes_admissibles") or {}
    return {
        "h_initial_bits": d["h_initial_bits"],
        "borne_h_initial": d["borne_h_initial"],
        "h_finale_bits": d["h_finale_bits"],
        "borne_h_finale": d["borne_h_finale"],
        "delta_h_total_bits": d["h_initial_bits"] - d["h_finale_bits"],
        "trace_resserrement": [
            {
                "titre": e["titre"],
                "delta_h_bits": e["delta_h_bits"],
                "borne_delta_h": e["borne_delta_h"],
                "noeuds_tues": list(e["noeuds_tues"]),
                "rattache": e["rattache"],
            }
            for e in d["trace_resserrement"]
        ],
        "booleanization_cost": {
            "booleanization_cost_bits": bc.get("booleanization_cost_bits", 0.0),
            "borne_cout": bc.get("borne_cout", 0.0),
            "etalon_or_bits": bc.get("etalon_or_bits",
                                     COUT_BOOLEANISATION_ETALON),
            "ratio_vs_etalon": bc.get("ratio_vs_etalon", 0.0),
        },
        "routes_admissibles": {
            "nb_routes_admissibles": rt.get("nb_routes_admissibles", 0),
            "nb_couches": rt.get("nb_couches", 0),
            "taux_branchement_mesure_bits":
                rt.get("taux_branchement_mesure_bits"),
        },
        "nb_noeuds_partition": len(d["partition"]),
        "note": ("Bits d'attention de l'instrument sur la route propre de "
                 "ce nœud — jamais une probabilité."),
    }


def attacher_entropie(resultat: ResultatSonde, registre: RegistreSondes,
                      posterieurs: Dict[str, float]) -> ResultatSonde:
    """Attache la lentille : section complète au mécanisme, section compacte
    à chaque nœud de la route A. Import direct d'entropie.py — aucun subprocess.

    Appelée par le CLI en mode JSON uniquement : le mode console reste rapide
    (comportement par défaut inchangé). Les nœuds partagés avec le registre
    sont COPIÉS avant rattachement (pas de pollution inter-appels).
    """
    import copy as _copy
    # Section complète du mécanisme lui-même.
    try:
        plein = entropie_depuis_sonde(
            resultat.mecanisme, registre=registre,
            posterieurs=posterieurs).vers_dict()
    except Exception as e:
        plein = {"erreur": f"lentille non calculable : {e}"}
    resultat.entropie = plein
    # Sections compactes par nœud (dédupliquées par nom), sur des copies.
    copies: Dict[int, Hypothese] = {}
    for h in resultat.route_a:
        if id(h) in copies:
            continue
        h2 = _copy.copy(h)
        copies[id(h)] = h2
        h2.section_entropie = section_entropie_noeud(h.nom, registre,
                                                    posterieurs)
    if copies:
        remap = lambda h: copies.get(id(h), h)
        resultat.route_a = [remap(h) for h in resultat.route_a]
        resultat.distance_fermeture = [remap(h) for h in resultat.distance_fermeture]
        resultat.levees = [remap(h) for h in resultat.levees]
        resultat.terra_incognita = [remap(h) for h in resultat.terra_incognita]
    return resultat


# ────────────────────────────────────────────────────────
# 2. PHI OU-ALLER — liste d'attention ordonnée (pas un score)
# ────────────────────────────────────────────────────────

def _mecanismes_ou_aller(registre: RegistreSondes) -> List[str]:
    """L'ensemble sondé : les 6 sorrys du Master + les hypothèses §1.

    Dédupliqué (clés H uniquement, pas les alias de noms) pour ne sonder
    chaque objet qu'une fois.
    """
    mecanismes = list(SORRY_VERS_TROU.keys())
    vus_objets = set()
    for cle in sorted(registre.hypotheses):
        if not cle.startswith("H"):
            continue
        h = registre.hypotheses[cle]
        if id(h) in vus_objets:
            continue  # alias : même objet déjà couvert
        vus_objets.add(id(h))
        mecanismes.append(cle)
    return mecanismes


def construire_ou_aller(registre: RegistreSondes,
                        posterieurs: Dict[str, float]) -> dict:
    """Mesures typées par mécanisme, TRIÉES par coût de booléanisation décroissant.

    Chaque entrée : mécanisme, type, statut typé, trous, ΔH totale (bits),
    coût de booléanisation (bits + ratio en étalons-or), routes admissibles,
    double verdict (oui/non). Le tri n'est PAS un score et PAS une P(A) :
    c'est l'ordre où le typage porte le plus de bits — où regarder en premier.
    """
    entrees: List[dict] = []
    for mec in _mecanismes_ou_aller(registre):
        try:
            s = sonder(mec, registre)
        except Exception as e:
            entrees.append({"mecanisme": mec, "erreur": str(e)[:120],
                            "cout_booleanisation_bits": 0.0,
                            "delta_h_total_bits": 0.0})
            continue
        try:
            d = entropie_depuis_sonde(mec, registre=registre,
                                      posterieurs=posterieurs).vers_dict()
        except Exception as e:
            d = {"notes": [f"lentille non calculable : {e}"]}
        bc = d.get("booleanization_cost") or {}
        rt = d.get("routes_admissibles") or {}
        cout = float(bc.get("booleanization_cost_bits", 0.0) or 0.0)
        h_init = float(d.get("h_initial_bits", 0.0) or 0.0)
        h_fin = float(d.get("h_finale_bits", 0.0) or 0.0)
        entrees.append({
            "mecanisme": mec,
            "type_mecanisme": s.type_mecanisme,
            "statut": s.statut_mecanisme,
            "trous": list(s.trous),
            "nb_noeuds_route": len(s.route_a),
            "nb_obstructions": len(s.obstructions_b),
            "double_verdict": bool(s.doubles_verdicts),
            "delta_h_total_bits": h_init - h_fin,
            "cout_booleanisation_bits": cout,
            "ratio_etalons_or": float(bc.get("ratio_vs_etalon", 0.0) or 0.0),
            "nb_routes_admissibles": rt.get("nb_routes_admissibles", 0),
        })
    # Tri : coût de booléanisation décroissant, puis ΔH décroissante,
    # puis nom (déterminisme total — jamais un score).
    entrees.sort(key=lambda e: (-e["cout_booleanisation_bits"],
                                -e["delta_h_total_bits"],
                                e["mecanisme"]))
    return {
        "commande": "ou-aller",
        "tri": ("cout_booleanisation_bits decroissant — ordre d'attention, "
                "PAS un score, PAS une P(A)"),
        "nb_mecanismes": len(entrees),
        "entrees": entrees,
        "lecture": (
            "En tête : là où l'effondrement du typage en booléen détruirait "
            "le plus de bits — où le typage porte le plus d'information, donc "
            "où regarder en premier. L'instrument montre où regarder ; il ne "
            "décide pas. Le veto de Tomy tranche."
        ),
        "etalon_or_bits": COUT_BOOLEANISATION_ETALON,
        "limites": LIMITE_ANCRAGE,
        "interdiction": INTERDICTION_TEXTE,
        "version_phi": VERSION,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }


def rendre_ou_aller_console(data: dict) -> str:
    """Rendu lisible : la liste d'attention ordonnée."""
    L = []
    L.append("╔" + "═" * 62 + "╗")
    L.append("║" + "PHI-COMPLEXITY — OÙ ALLER 🧭".center(62) + "║")
    L.append("║" + "liste d'attention ordonnée — l'instrument montre,".center(62) + "║")
    L.append("║" + "le veto de Tomy tranche".center(62) + "║")
    L.append("╚" + "═" * 62 + "╝")
    L.append("")
    L.append(f"{data['nb_mecanismes']} mécanismes sondés")
    L.append(f"Tri : {data['tri']}")
    L.append(f"étalon-or (1−H_φ) = {data['etalon_or_bits']:.6f} bits")
    L.append("")
    L.append("┌─ OÙ REGARDER EN PREMIER "
             "(coût booléen = bits que l'effondrement détruirait)")
    for i, e in enumerate(data["entrees"], 1):
        if "erreur" in e:
            L.append(f"│  {i:>2}. {e['mecanisme']:<32} — erreur : {e['erreur']}")
            continue
        dv = " ⑂" if e["double_verdict"] else ""
        L.append(
            f"│  {i:>2}. {e['mecanisme']:<32}{dv} "
            f"{e['cout_booleanisation_bits']:8.4f} bits "
            f"({e['ratio_etalons_or']:6.2f} étalons) "
            f"ΔH={e['delta_h_total_bits']:+.4f} "
            f"routes={e['nb_routes_admissibles']}"
        )
        L.append(f"│      [{e['type_mecanisme']}] {e['statut'][:72]}")
    L.append("└" + "─" * 61)
    L.append("")
    L.append("Lecture : " + data["lecture"])
    L.append("")
    L.append(INTERDICTION_TEXTE)
    L.append("Limites : " + LIMITE_ANCRAGE[:260] + "… (voir ANCRAGE_ENTROPIE_20260930.md)")
    return "\n".join(L)
