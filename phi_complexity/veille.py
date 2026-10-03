"""
veille.py — La Veille : moniteur de régression structurelle (v0.14.0).

Idée (Tomy) : « nous saurons si nous avons déconstruit par manque de
prudence nos développements » — comparer la carte d'aujourd'hui à une
référence et détecter les dégradations silencieuses.

Deux commandes :
- `phi snapshot <dossier> --out ref.json` : fige la carte croyante
  (`chemins_croyants`) + les arêtes d'imports résolues dans une enveloppe
  datée, versionnée et signée (md5).
- `phi veille <dossier> --ref ref.json` : recalcule la carte, la compare
  à la référence et rend un verdict typé : STABLE, DÉGRADATION DÉTECTÉE
  ou INSTRUMENT DÉGRADÉ — jamais un booléen seul sans le détail.
  INSTRUMENT DÉGRADÉ (durcissement 2026-10-01) : l'arrière-plan d'analyse
  a été perdu (ex. tree-sitter-language-pack effacé par un reboot) —
  la veille refuse de comparer du vide à la référence (exit 3),
  au lieu d'émettre une fausse « dégradation ».

Règles d'honnêteté :
- un symbole renommé (disparu + apparu, même fichier, lignes proches)
  est signalé `renommage_probable`, pas compté comme perte + gain ;
- une référence prise avec une autre version de phi → avertissement
  explicite, jamais de comparaison silencieuse ;
- les fichiers sont comparés par chemins RELATIFS au dossier : le projet
  peut avoir déménagé entre le snapshot et la veille.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .core import VERSION
from .croyances import chemins_croyants
# Réutilisation interne (même paquet) : le graphe d'imports déjà construit
# par `chemins_croyants` n'est pas exposé ; on le recalcule à l'identique.
from .croyances import _graphe_imports  # noqa: F401  (usage interne documenté)
from .editeur.indexeur import _fichiers_supportes, EXCLUSIONS_DEFAUT

# ────────────────────────────────────────────────────────
# CONSTANTES DE JUGEMENT (un seul endroit, documentées)
# ────────────────────────────────────────────────────────

#: Écart de lignes maximal pour suspecter un renommage plutôt
#: qu'une suppression suivie d'un ajout (heuristique explicite).
SEUIL_RENOMMAGE_LIGNES = 10

#: Seuil de variation du postérieur en dessous duquel on considère
#: qu'il n'a pas bougé (bruit de représentation flottante).
SEUIL_DERIVE_POSTERIEUR = 1e-9

#: Nombre de dérives postérieures rapportées en détail.
TOP_DERIVES = 12

#: Seuil de divergence entre extracteurs Lean au-delà duquel la veille
#: le signale explicitement (durcissement 2026-10-02). Vaut 1 : TOUTE
#: divergence est rapportée — un seuil plus haut réintroduirait du
#: silence, exactement ce que le durcissement interdit. Le signal ne
#: fait PAS basculer le verdict (contrairement à INSTRUMENT DÉGRADÉ) :
#: la comparaison reste valide, seuls les symboles récupérés ont des
#: métriques proxy (complexité = lignes).
SEUIL_DIVERGENCE_EXTRACTEURS = 1


# ────────────────────────────────────────────────────────
# SANTÉ DE L'INSTRUMENT (durcissement 2026-10-01)
# ────────────────────────────────────────────────────────
# Le 2026-09-30, `phi veille` a rendu une fausse « dégradation »
# (3531 → 33 symboles) : un reboot avait effacé `tree-sitter-language-pack`
# de /usr/local, les 214 fichiers .lean n'étaient plus analysés, et la
# veille a comparé du vide à la référence au lieu de refuser.
# Règle : un arrière-plan perdu n'est JAMAIS une dégradation du code —
# c'est l'instrument qui est dégradé, et il échoue bruyamment.

def _causes_arriere_plan(dossier: str, exclusions,
                         exts_attendues) -> List[str]:
    """Fichiers présents mais SANS analyseur fonctionnel, pour des
    extensions que l'on s'attend à analyser.

    `exts_attendues` : extensions analysées dans la référence (veille),
    ou toutes les extensions supportées (snapshot — une baseline prise
    sans l'arrière-plan serait une baseline dégradée).
    Retourne des causes lisibles, [] si l'instrument est sain.
    """
    from .editeur.indexeur import fichiers_non_supportes
    causes: List[str] = []
    par_ext: Dict[str, List[str]] = {}
    for ns in fichiers_non_supportes(dossier, exclusions):
        par_ext.setdefault(ns.get("extension", "?"), []).append(
            ns.get("raison", "?"))
    for ext in sorted(par_ext):
        if ext in exts_attendues:
            raisons = sorted(set(par_ext[ext]))
            causes.append(
                f"{len(par_ext[ext])} fichier(s) '{ext}' sans analyseur "
                f"fonctionnel ({'; '.join(raisons)})"
            )
    return causes


def _sante_instrument(ref: dict, dossier_courant: str,
                      exclusions) -> Tuple[bool, List[str]]:
    """L'instrument peut-il comparer honnêtement au `ref` ?

    Retourne (dégradé, causes). Dégradé = des fichiers d'extensions
    analysées dans la référence ne sont plus analysables aujourd'hui
    (arrière-plan perdu : tree-sitter effacé, grammaire indisponible…).
    """
    exts_ref = set()
    for s in ref.get("carte", {}).get("symboles", []):
        ext = os.path.splitext(s.get("fichier", ""))[1].lower()
        if ext:
            exts_ref.add(ext)
    causes = _causes_arriere_plan(dossier_courant, exclusions, exts_ref)
    return (bool(causes), causes)


# ────────────────────────────────────────────────────────
# SNAPSHOT
# ────────────────────────────────────────────────────────

def _aretes_resolues(dossier_abs: str, exclusions) -> Dict[str, List[str]]:
    """
    Arêtes d'imports résolues : {fichier_rel: [cibles_rel triées]}.

    Recalcule le même graphe que `chemins_croyants` (même fonction
    interne, mêmes exclusions) pour que la comparaison soit à
    périmètre identique.
    """
    fichiers = _fichiers_supportes(dossier_abs, exclusions)
    graphe, _non_resolus = _graphe_imports(dossier_abs, fichiers)
    aretes: Dict[str, List[str]] = {}
    for src_abs, dsts in graphe.items():
        src_rel = os.path.relpath(src_abs, dossier_abs)
        aretes[src_rel] = sorted(os.path.relpath(d, dossier_abs) for d in dsts)
    return aretes


def _md5_carte(carte: dict) -> str:
    """Empreinte de la carte sur sa forme canonique (clés triées)."""
    canonique = json.dumps(carte, sort_keys=True, ensure_ascii=True)
    return hashlib.md5(canonique.encode("utf-8")).hexdigest()


def prendre_snapshot(dossier: str, exclusions=None) -> dict:
    """
    Fige l'état structurel d'un dossier : carte croyante + arêtes.

    Retourne l'enveloppe complète (JSON-sérialisable) :
    outil, commande, version_phi, date (UTC, ISO), dossier (absolu),
    md5_carte, carte, aretes.
    """
    dossier_abs = os.path.normpath(os.path.abspath(dossier))
    carte = chemins_croyants(dossier, exclusions=exclusions, complet=True)
    enveloppe = {
        "outil": "phi",
        "commande": "snapshot",
        "version_phi": VERSION,
        "date": datetime.now(timezone.utc).isoformat(),
        "dossier": dossier_abs,
        "md5_carte": _md5_carte(carte),
        "carte": carte,
        "aretes": _aretes_resolues(dossier_abs, exclusions),
    }
    return enveloppe


def ecrire_snapshot(dossier: str, chemin_sortie: str, exclusions=None) -> dict:
    """Prend un snapshot et l'écrit sur disque. Retourne l'enveloppe."""
    enveloppe = prendre_snapshot(dossier, exclusions=exclusions)
    with open(chemin_sortie, "w", encoding="utf-8") as f:
        json.dump(enveloppe, f, ensure_ascii=False, indent=2)
    return enveloppe


def charger_reference(chemin: str) -> dict:
    """
    Charge et valide une enveloppe de référence.

    Lève ValueError avec un message explicite si le fichier est
    absent, illisible ou n'est pas une enveloppe `phi snapshot`.
    """
    if not os.path.isfile(chemin):
        raise ValueError(f"référence introuvable : {chemin}")
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            enveloppe = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"référence illisible ({chemin}) : {exc}") from exc
    if not isinstance(enveloppe, dict) or enveloppe.get("commande") != "snapshot":
        raise ValueError(
            f"{chemin} n'est pas une enveloppe `phi snapshot` "
            "(champ 'commande' manquant ou différent)."
        )
    for champ in ("version_phi", "date", "dossier", "carte", "aretes"):
        if champ not in enveloppe:
            raise ValueError(
                f"enveloppe invalide : champ '{champ}' manquant dans {chemin}."
            )
    return enveloppe


# ────────────────────────────────────────────────────────
# DIFF
# ────────────────────────────────────────────────────────

def _index_symboles(carte: dict) -> Dict[Tuple[str, str], dict]:
    """
    Indexe les symboles par (fichier_rel, nom).

    En cas de collision (même nom deux fois dans un fichier — rare),
    la clé est désambiguïsée par la ligne : (fichier, nom, ligne).
    La désambiguïsation est DÉTERMINISTE : tri par (fichier, ligne)
    avant indexation, sinon l'ordre de tri (postérieur) ferait diverger
    les clés entre deux cartes et créerait de faux « perdus »
    (bug trouvé le 2026-10-03 sur Bibliotheque-Aperiodique-Lean4).
    """
    index: Dict[Tuple[str, str], dict] = {}
    symboles = sorted(carte.get("symboles", []),
                      key=lambda s: (s["fichier"], s["ligne"]))
    for s in symboles:
        cle = (s["fichier"], s["nom"])
        if cle in index:
            cle = (s["fichier"], s["nom"], s["ligne"])
        index[cle] = s
    return index


def _localiser(nom: str, fichier: str, ligne: int) -> str:
    return f"{fichier}:{ligne} — {nom}"


def comparer(ref: dict, dossier_courant: str, exclusions=None) -> dict:
    """
    Compare la carte courante d'un dossier à une enveloppe de référence.

    Retourne le diff complet avec `verdict` typé en tête :
    "STABLE" (aucune dégradation) ou "DÉGRADATION DÉTECTÉE" (+ signaux).
    Les dérives informatives (gains propres, réparations, nouvelles arêtes)
    ne font jamais basculer le verdict. En revanche, un trou dans un
    NOUVEAU symbole est un signal dur (durcissement 2026-10-02) : une
    affirmation non vérifiée qui apparaît, même dans du code neuf,
    est une dégradation de l'état de preuve.
    """
    dossier_abs = os.path.normpath(os.path.abspath(dossier_courant))
    avertissements: List[str] = []

    version_ref = ref.get("version_phi")
    if version_ref != VERSION:
        avertissements.append(
            f"référence prise avec phi {version_ref}, analyse avec phi "
            f"{VERSION} : comparaison à interpréter avec prudence "
            "(capteurs ou formule ont pu changer)."
        )
    if os.path.normpath(ref.get("dossier", "")) != dossier_abs:
        avertissements.append(
            "le dossier a changé d'emplacement depuis le snapshot "
            f"({ref.get('dossier')} → {dossier_abs}) : comparaison par "
            "chemins relatifs, les renommages de dossiers sont invisibles."
        )

    carte = chemins_croyants(dossier_courant, exclusions=exclusions,
                             complet=True)
    aretes_cur = _aretes_resolues(dossier_abs, exclusions)

    ref_syms = _index_symboles(ref["carte"])
    cur_syms = _index_symboles(carte)
    cles_ref = set(ref_syms)
    cles_cur = set(cur_syms)
    communes = cles_ref & cles_cur
    cles_perdues = cles_ref - cles_cur
    cles_gagnees = cles_cur - cles_ref

    # — Renommages probables : perdu + gagné, même fichier, lignes proches.
    renommages: List[dict] = []
    perdus_restants = set(cles_perdues)
    gagnes_restants = set(cles_gagnees)
    for cle_p in sorted(cles_perdues):
        fichier_p = cle_p[0]
        ligne_p = ref_syms[cle_p]["ligne"]
        meilleur = None
        meilleur_delta = SEUIL_RENOMMAGE_LIGNES + 1
        for cle_g in sorted(gagnes_restants):
            if cle_g[0] != fichier_p:
                continue
            delta = abs(cur_syms[cle_g]["ligne"] - ligne_p)
            if delta < meilleur_delta:
                meilleur, meilleur_delta = cle_g, delta
        if meilleur is not None:
            nom_p = ref_syms[cle_p]["nom"]
            nom_g = cur_syms[meilleur]["nom"]
            if nom_p != nom_g:  # même nom, lignes proches = déplacement, pas renommage
                renommages.append({
                    "fichier": fichier_p,
                    "ancien_nom": nom_p,
                    "nouveau_nom": nom_g,
                    "ligne_avant": ligne_p,
                    "ligne_apres": cur_syms[meilleur]["ligne"],
                    "delta_ligne": cur_syms[meilleur]["ligne"] - ligne_p,
                })
                perdus_restants.discard(cle_p)
                gagnes_restants.discard(meilleur)

    def _fiche(cle, table):
        s = table[cle]
        return {"nom": s["nom"], "fichier": s["fichier"],
                "ligne": s["ligne"], "complexite": s["complexite"],
                "posterior": round(s["posterior"], 4)}

    symboles_perdus = [_fiche(c, ref_syms) for c in sorted(perdus_restants)]
    symboles_gagnes = []
    for c in sorted(gagnes_restants):
        fiche = _fiche(c, cur_syms)
        fiche["est_trou"] = bool(cur_syms[c]["sorry_present"])
        symboles_gagnes.append(fiche)

    # — Durcissement 2026-10-02 : un trou dans un NOUVEAU symbole est un
    # signal dur, pas une simple information de gain. Une affirmation non
    # vérifiée qui apparaît est une dégradation de l'état de preuve,
    # qu'elle soit dans du code neuf ou existant.
    trous_nouveaux_symboles = [s for s in symboles_gagnes if s["est_trou"]]

    nouveaux_trous = []
    trous_repares = []
    for cle in sorted(communes):
        avant = bool(ref_syms[cle]["sorry_present"])
        apres = bool(cur_syms[cle]["sorry_present"])
        if apres and not avant:
            fiche = _fiche(cle, cur_syms)
            fiche["posterior_avant"] = round(ref_syms[cle]["posterior"], 4)
            nouveaux_trous.append(fiche)
        elif avant and not apres:
            trous_repares.append(_fiche(cle, cur_syms))

    # — Arêtes : imports résolus dans la ref, non résolus aujourd'hui.
    aretes_ref: Dict[str, List[str]] = ref.get("aretes", {})
    aretes_cassees: List[dict] = []
    aretes_nouvelles: List[dict] = []
    for src, dsts in aretes_ref.items():
        cur_dsts = set(aretes_cur.get(src, []))
        for dst in dsts:
            if dst not in cur_dsts:
                aretes_cassees.append({
                    "fichier": src, "cible": dst,
                    "fichier_supprime": src not in aretes_cur,
                })
    for src, dsts in aretes_cur.items():
        ref_dsts = set(aretes_ref.get(src, []))
        for dst in dsts:
            if dst not in ref_dsts:
                aretes_nouvelles.append({"fichier": src, "cible": dst})

    # — Dérive de couplage : fichiers dont le blast radius max a gonflé.
    def _couplage_max(carte_locale):
        par_fichier: Dict[str, int] = {}
        for s in carte_locale.get("symboles", []):
            f = s["fichier"]
            par_fichier[f] = max(par_fichier.get(f, 0),
                                 int(s.get("dependants_aval", 0)))
        return par_fichier

    couplage_ref = _couplage_max(ref["carte"])
    couplage_cur = _couplage_max(carte)
    derive_couplage = []
    for f in sorted(set(couplage_ref) & set(couplage_cur)):
        delta = couplage_cur[f] - couplage_ref[f]
        if delta != 0:
            derive_couplage.append({"fichier": f, "avant": couplage_ref[f],
                                    "apres": couplage_cur[f], "delta": delta})
    derive_couplage.sort(key=lambda d: -abs(d["delta"]))

    # — Dérive postérieure : symboles communs dont le score a le plus bougé.
    derive_posterieure = []
    for cle in communes:
        delta = cur_syms[cle]["posterior"] - ref_syms[cle]["posterior"]
        if abs(delta) > SEUIL_DERIVE_POSTERIEUR:
            fiche = _fiche(cle, cur_syms)
            fiche["posterior_avant"] = round(ref_syms[cle]["posterior"], 4)
            fiche["delta"] = round(delta, 4)
            derive_posterieure.append(fiche)
    derive_posterieure.sort(key=lambda d: -abs(d["delta"]))
    derive_posterieure = derive_posterieure[:TOP_DERIVES]

    # — Santé de l'instrument (durcissement 2026-10-01) : un arrière-plan
    # perdu (ex. tree-sitter effacé par un reboot) n'est JAMAIS une
    # « dégradation » du code — c'est l'instrument qui est dégradé.
    # Le verdict bascule sur INSTRUMENT DÉGRADÉ, jamais STABLE.
    instrument_degrade, causes_instrument = _sante_instrument(
        ref, dossier_courant, exclusions)

    # — Divergence d'extracteurs (durcissement 2026-10-02) : fichiers où
    # le repli robuste a récupéré des symboles invisibles à tree-sitter
    # (cause racine : `|expr|` en position de type). Signal EXPLICITE
    # et bruyant — jamais un silence. Ne fait pas basculer le verdict :
    # la comparaison reste valide (les symboles récupérés participent
    # au diff, y compris la détection de trous).
    extraction_repli = {f: n for f, n in
                        carte.get("extraction_repli", {}).items()
                        if n >= SEUIL_DIVERGENCE_EXTRACTEURS}

    # — Verdict : seules les dégradations dures le font basculer.
    signaux = []
    if nouveaux_trous:
        signaux.append(f"{len(nouveaux_trous)} nouveau(x) trou(s)")
    if trous_nouveaux_symboles:
        signaux.append(
            f"{len(trous_nouveaux_symboles)} nouveau(x) trou(s) "
            f"dans de nouveaux symboles")
    if symboles_perdus:
        signaux.append(f"{len(symboles_perdus)} symbole(s) perdu(s)")
    if aretes_cassees:
        signaux.append(f"{len(aretes_cassees)} arête(s) d'import cassée(s)")
    if instrument_degrade:
        verdict = "INSTRUMENT DÉGRADÉ"
    else:
        verdict = "DÉGRADATION DÉTECTÉE" if signaux else "STABLE"

    return {
        "verdict": verdict,
        "instrument_degrade": instrument_degrade,
        "causes_instrument": causes_instrument,
        "signaux": signaux,
        "avertissements": avertissements,
        "dossier_courant": dossier_abs,
        "dossier_reference": ref.get("dossier"),
        "date_reference": ref.get("date"),
        "version_reference": version_ref,
        "version_courante": VERSION,
        "md5_reference": ref.get("md5_carte"),
        "nouveaux_trous": nouveaux_trous,
        "trous_nouveaux_symboles": trous_nouveaux_symboles,
        "trous_repares": trous_repares,
        "symboles_perdus": symboles_perdus,
        "symboles_gagnes": symboles_gagnes,
        "renommage_probable": renommages,
        "aretes_cassees": aretes_cassees,
        "aretes_nouvelles": aretes_nouvelles,
        "derive_couplage": derive_couplage,
        "derive_posterieure": derive_posterieure,
        "nb_symboles_reference": len(ref_syms),
        "nb_symboles_courant": len(cur_syms),
        # Durcissement 2026-10-02 : {fichier: n} où le repli robuste a
        # récupéré n symboles invisibles à tree-sitter. Vide = nominal.
        "extraction_repli": extraction_repli,
    }


# ────────────────────────────────────────────────────────
# RENDU CONSOLE
# ────────────────────────────────────────────────────────

def _section_console(titre: str, items: list, formater) -> List[str]:
    lignes = [f"  {titre} ({len(items)})"]
    for it in items[:15]:
        lignes.append(f"    • {formater(it)}")
    if len(items) > 15:
        lignes.append(f"    … et {len(items) - 15} autres")
    return lignes


def veille_console(diff: dict) -> str:
    """Rend le diff lisible dans un terminal."""
    verdict = diff["verdict"]
    if verdict == "INSTRUMENT DÉGRADÉ":
        # Durcissement 2026-10-01 : jamais de ✅ sur un instrument en cause.
        bandeau = "🚨 INSTRUMENT DÉGRADÉ — pas de verdict (l'instrument est en cause, pas le code)"
    else:
        bandeau = ("✅" if verdict == "STABLE" else "🚨") + f" {verdict}"
    lignes = [
        "╔════════════════════════════════════════════════════════════════════╗",
        "║              PHI-COMPLEXITY — LA VEILLE  👁                        ║",
        "╚════════════════════════════════════════════════════════════════════╝",
        "",
        bandeau,
    ]
    causes = diff.get("causes_instrument", [])
    if causes:
        for cause in causes:
            lignes.append(f"  🔧 {cause}")
        lignes.append("     → réparez l'arrière-plan (ex. réinstallez "
                      "tree-sitter-language-pack dans le venv phi), "
                      "puis relancez la veille.")
    if diff["signaux"]:
        lignes.append("Signaux : " + " ; ".join(diff["signaux"]))
    for av in diff["avertissements"]:
        lignes.append(f"⚠️  {av}")
    lignes.append(
        f"Symboles : {diff['nb_symboles_reference']} (réf. "
        f"{diff['date_reference']}) → {diff['nb_symboles_courant']} (courant)"
    )
    lignes.append("")
    if diff["nouveaux_trous"]:
        lignes += _section_console(
            "NOUVEAUX TROUS", diff["nouveaux_trous"],
            lambda s: f"{s['fichier']}:{s['ligne']} — {s['nom']} "
                      f"(postérieur {s['posterior']})")
    if diff.get("trous_nouveaux_symboles"):
        lignes += _section_console(
            "NOUVEAUX TROUS DANS DE NOUVEAUX SYMBOLES",
            diff["trous_nouveaux_symboles"],
            lambda s: f"{s['fichier']}:{s['ligne']} — {s['nom']} "
                      f"(postérieur {s['posterior']})")
    if diff.get("extraction_repli"):
        # Durcissement 2026-10-02 : la cécité partielle de tree-sitter
        # (ex. `|expr|` en position de type) est SIGNALÉE, jamais tue.
        # Les symboles récupérés participent au diff avec des métriques
        # proxy (complexité = lignes) — la comparaison reste valide.
        total_recup = sum(diff["extraction_repli"].values())
        lignes.append(
            f"  ⚠️  EXTRACTION DÉGRADÉE — repli robuste : "
            f"{total_recup} symbole(s) récupéré(s) dans "
            f"{len(diff['extraction_repli'])} fichier(s) "
            f"(invisibles à tree-sitter, métriques = proxy lignes)")
        for f in sorted(diff["extraction_repli"])[:15]:
            lignes.append(f"    • {f} : "
                          f"{diff['extraction_repli'][f]} symbole(s) récupéré(s)")
        if len(diff["extraction_repli"]) > 15:
            lignes.append(f"    … et {len(diff['extraction_repli']) - 15} autres")
    if diff["symboles_perdus"]:
        lignes += _section_console(
            "SYMBOLES PERDUS ⚠️", diff["symboles_perdus"],
            lambda s: f"{s['fichier']}:{s['ligne']} — {s['nom']}")
    if diff["aretes_cassees"]:
        lignes += _section_console(
            "ARÊTES CASSÉES", diff["aretes_cassees"],
            lambda a: f"{a['fichier']} → {a['cible']}"
                      + (" (fichier importeur supprimé)"
                         if a["fichier_supprime"] else ""))
    if diff["renommage_probable"]:
        lignes += _section_console(
            "RENOMMAGES PROBABLES (pas des pertes)", diff["renommage_probable"],
            lambda r: f"{r['fichier']} : {r['ancien_nom']} → {r['nouveau_nom']} "
                      f"(l.{r['ligne_avant']} → l.{r['ligne_apres']})")
    if diff["trous_repares"]:
        lignes += _section_console(
            "TROUS RÉPARÉS ✓", diff["trous_repares"],
            lambda s: f"{s['fichier']}:{s['ligne']} — {s['nom']}")
    if diff["derive_couplage"]:
        lignes += _section_console(
            "DÉRIVE DE COUPLAGE (informatif)", diff["derive_couplage"],
            lambda d: f"{d['fichier']} : blast max {d['avant']} → {d['apres']} "
                      f"({d['delta']:+d})")
    if diff["derive_posterieure"]:
        lignes += _section_console(
            "DÉRIVES POSTÉRIEURES (informatif)", diff["derive_posterieure"],
            lambda d: f"{d['fichier']}:{d['ligne']} — {d['nom']} : "
                      f"{d['posterior_avant']} → {d['posterior']} "
                      f"({d['delta']:+.2f})")
    if diff["symboles_gagnes"]:
        n_trous = sum(1 for s in diff["symboles_gagnes"] if s["est_trou"])
        lignes.append(f"  Symboles gagnés (informatif) : "
                      f"{len(diff['symboles_gagnes'])} "
                      f"(dont {n_trous} avec trou)")
    if not any([diff["nouveaux_trous"], diff.get("trous_nouveaux_symboles"),
                diff["symboles_perdus"],
                diff["aretes_cassees"], diff["renommage_probable"],
                diff["trous_repares"], diff["derive_couplage"],
                diff["derive_posterieure"], diff["symboles_gagnes"],
                diff["aretes_nouvelles"], diff.get("extraction_repli")]):
        lignes.append("  Rien n'a bougé : la structure est identique.")
    return "\n".join(lignes)
