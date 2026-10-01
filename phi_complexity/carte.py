"""
carte.py — La Carte du projet : index des symboles + santé phi, en une seule passe.

`carte_projet(dossier)` construit la structure complète :
- par fichier : symboles (nom, ligne, complexité), radiance, statut gnostique, oudjat ;
- collisions : noms de symboles définis dans ≥ 2 fichiers distincts ;
- oudjat suprême : symbole de complexité maximale du projet.

La structure retournée est JSON-sérialisable telle quelle, avec des clés
stables — pensée pour être requêtée par un humain (`phi index`) comme par
un agent (`phi index --format json | jq ...`).

Réutilise `editeur.indexeur.indexer_projet` (aucune duplication) et `auditer()`
fichier par fichier avec le même garde-fou try/except que `auditer_projet`.

v0.8.0 : `carte_projet(..., complet=False)` = phase 1 — symboles sans
métriques (radiance/statut/oudjat par fichier = None, `metriques_calculees`
= False, clé `mode` = "rapide"). En mode complet, le schéma est EXACTEMENT
celui de v0.7.0 (aucune clé ajoutée) : valeurs inchangées.
"""
import os
from typing import Dict, List, Optional

from .core import VERSION
from .editeur.indexeur import indexer_projet, fichiers_non_supportes, Symbole
from .langs import treesitter_disponible, EXTENSIONS_TREESITTER


def _avertissements_backend() -> List[str]:
    """
    Avertissements honnêtes sur le backend d'analyse — jamais de silence.

    Si `tree-sitter-language-pack` est absent, toutes les extensions mappées
    sur Tree-sitter (dont `.lean`) sont reconnues mais sans analyseur
    fonctionnel : l'instrument le dit explicitement au lieu de rendre
    une carte vide qui ressemble à un projet vide.
    """
    if treesitter_disponible():
        return []
    extensions = sorted(set(EXTENSIONS_TREESITTER))
    return [
        "'tree-sitter-language-pack' non installé : seuls les langages natifs "
        "(python) sont analysés.",
        f"Extensions reconnues mais NON analysées ({len(extensions)}) : "
        + ", ".join(extensions) + ".",
        "Installation : pip install phi-complexity[multilang] "
        "(ou : pip install tree-sitter-language-pack).",
    ]


def _statut_gnostique(radiance: float) -> str:
    """Statut gnostique d'une radiance (mêmes seuils que `auditer_projet`)."""
    if radiance >= 85:
        return "HERMÉTIQUE ✦"
    if radiance >= 60:
        return "EN ÉVEIL ◈"
    return "DORMANT ░"


def _symbole_vers_dict(s: Symbole) -> dict:
    """Sérialisation stable d'un symbole pour la carte / le JSON."""
    return {
        "nom": s.nom,
        "ligne": int(s.ligne),
        "complexite": int(s.complexite),
    }


def _detecter_collisions(index: Dict[str, List[Symbole]]) -> List[dict]:
    """
    Regroupe les symboles par nom et retourne ceux définis dans
    ≥ 2 fichiers distincts, triés par nom. Chaque occurrence porte
    son fichier, sa ligne et sa complexité (triés par fichier, ligne).
    """
    par_nom: Dict[str, List[Symbole]] = {}
    for symboles in index.values():
        for s in symboles:
            par_nom.setdefault(s.nom, []).append(s)
    collisions = []
    for nom in sorted(par_nom):
        occurrences = par_nom[nom]
        fichiers_distincts = {s.fichier for s in occurrences}
        if len(fichiers_distincts) >= 2:
            occs = sorted(
                (_symbole_vers_dict(s) | {"fichier": s.fichier} for s in occurrences),
                key=lambda o: (o["fichier"], o["ligne"]),
            )
            collisions.append({
                "nom": nom,
                "nb_occurrences": len(occs),
                "nb_fichiers": len(fichiers_distincts),
                "occurrences": occs,
            })
    return collisions


def _oudjat_supreme(index: Dict[str, List[Symbole]]) -> Optional[dict]:
    """Le symbole de complexité maximale du projet (avec son fichier)."""
    meilleur: Optional[Symbole] = None
    for symboles in index.values():
        for s in symboles:
            if meilleur is None or s.complexite > meilleur.complexite:
                meilleur = s
    if meilleur is None:
        return None
    return _symbole_vers_dict(meilleur) | {"fichier": meilleur.fichier}


def carte_projet(dossier: str, lang: Optional[str] = None,
                 exclusions=None, complet: bool = True) -> dict:
    """
    Cartographie un dossier : index des symboles + santé phi par fichier.

    `lang` force le langage d'analyse et d'audit (défaut : détection
    par extension). `exclusions` : `None` → exclusions par défaut
    (`.lake/`, `.git/`, `__pycache__/`, …) ; `[]` → aucune ; sinon
    liste explicite de noms de dossiers.
    `complet=False` (phase 1, `phi index --rapide`) : symboles seuls —
    aucune métrique calculée (`radiance`, `statut_gnostique`, `oudjat`
    par fichier et `radiance_globale` valent None, `metriques_calculees`
    = False). La clé `mode` = "rapide" (présente UNIQUEMENT en phase 1)
    dit honnêtement ce que la carte contient : un agent ne doit jamais
    confondre une radiance absente (mode rapide) avec un projet vide.

    Un fichier qui échoue à l'analyse ou à l'audit est ignoré — mais
    JAMAIS silencieusement : `non_supportes` liste chaque fichier sans
    analyseur fonctionnel (avec sa raison), et `avertissements` signale
    un backend Tree-sitter manquant. Un instrument qui tait ses angles
    morts n'est pas un instrument de confiance.

    Retourne un dict JSON-sérialisable. En mode complet, les clés sont
    EXACTEMENT celles de v0.7.0 (aucune clé ajoutée) : dossier, version_phi,
    nb_fichiers, nb_symboles, radiance_globale, statut_gnostique_global,
    fichiers, collisions, oudjat_supreme, non_supportes, avertissements.
    En mode rapide s'ajoutent : `mode` = "rapide", et par fichier
    `metriques_calculees` = False avec radiance/statut/oudjat à None.
    """
    from . import auditer  # import paresseux : évite l'import circulaire

    index = indexer_projet(dossier, langage=lang, exclusions=exclusions,
                           complet=complet)
    non_supportes = fichiers_non_supportes(dossier, exclusions=exclusions,
                                           langage=lang)
    avertissements = _avertissements_backend()

    fichiers = []
    radiances = []
    for chemin in sorted(index):
        symboles = sorted(index[chemin], key=lambda s: s.ligne)
        if not complet:
            # Phase 1 : pas d'audit — les métriques sont le coût, on les saute.
            fichiers.append({
                "fichier": chemin,
                "langage": symboles[0].langage if symboles else None,
                "radiance": None,
                "statut_gnostique": None,
                "metriques_calculees": False,
                "nb_symboles": len(symboles),
                "oudjat": None,
                "symboles": [_symbole_vers_dict(s) for s in symboles],
            })
            continue
        try:
            metriques = auditer(chemin, lang=lang)
        except Exception:
            continue
        langage_fichier = metriques.get("langage") or (symboles[0].langage if symboles else None)
        oudjat = metriques.get("oudjat")
        fichiers.append({
            "fichier": chemin,
            "langage": langage_fichier,
            "radiance": round(float(metriques["radiance"]), 2),
            "statut_gnostique": metriques.get("statut_gnostique")
                                or _statut_gnostique(float(metriques["radiance"])),
            "nb_symboles": len(symboles),
            "oudjat": (
                {"nom": oudjat["nom"], "ligne": int(oudjat["ligne"]),
                 "complexite": int(oudjat["complexite"])}
                if oudjat else None
            ),
            "symboles": [_symbole_vers_dict(s) for s in symboles],
        })
        radiances.append(float(metriques["radiance"]))

    if complet:
        radiance_globale = round(sum(radiances) / len(radiances), 2) if radiances else 60.0
        statut_global = _statut_gnostique(radiance_globale)
    else:
        # Honnêteté : aucune radiance calculée en phase 1 — None, pas 60.
        radiance_globale = None
        statut_global = None
    carte = {
        "dossier": dossier,
        "version_phi": VERSION,
        "nb_fichiers": len(fichiers),
        "nb_symboles": sum(f["nb_symboles"] for f in fichiers),
        "radiance_globale": radiance_globale,
        "statut_gnostique_global": statut_global,
        "fichiers": fichiers,
        "collisions": _detecter_collisions(index),
        "oudjat_supreme": _oudjat_supreme(index) if complet else None,
        "non_supportes": non_supportes,
        "avertissements": avertissements,
    }
    if not complet:
        # Clé rapide SEULEMENT en phase 1 : le mode complet restitue
        # EXACTEMENT le schéma historique (v0.7.0), sans clé ajoutée.
        carte["mode"] = "rapide"
    return carte


def carte_console(carte: dict) -> str:
    """Rend la carte lisible dans un terminal (tableaux + sections)."""
    if carte.get("mode") == "rapide":
        ligne_radiance = ("  ☼  RADIANCE  : non calculée (mode --rapide : "
                          "index seul, sans métriques)")
    else:
        ligne_radiance = (f"  ☼  RADIANCE  : {carte['radiance_globale']} / 100 "
                          f"({carte['statut_gnostique_global']})")
    lignes = [
        "╔════════════════════════════════════════════════════════════════════╗",
        "║            PHI-COMPLEXITY — CARTE DU PROJET  🗺                     ║",
        "╚════════════════════════════════════════════════════════════════════╝",
        "",
        f"  📁 Dossier   : {carte['dossier']}",
        f"  📚 Fichiers  : {carte['nb_fichiers']}    🔣 Symboles : {carte['nb_symboles']}",
        ligne_radiance,
        "",
    ]
    avertissements = carte.get("avertissements") or []
    if avertissements:
        lignes.append("  ⚠ AVERTISSEMENTS — l'instrument signale ses limites :")
        for a in avertissements:
            lignes.append(f"    ! {a}")
        lignes.append("")

    lignes += [
        "  TABLEAU DES MODULES :",
        "  " + "-" * 70,
        f"  {'Fichier':<36} | {'Langage':<10} | {'Radiance':<8} | {'Symb.':<5} | Oudjat",
        "  " + "-" * 70,
    ]
    for f in carte["fichiers"]:
        nom_court = os.path.basename(f["fichier"])
        if len(nom_court) > 34:
            nom_court = "…" + nom_court[-33:]
        oudjat = f["oudjat"]
        oudjat_txt = f"'{oudjat['nom']}' ({oudjat['complexite']})" if oudjat else "—"
        radiance_txt = f"{f['radiance']:>6.1f}" if f["radiance"] is not None else "   —  "
        lignes.append(
            f"  {nom_court:<36} | {str(f['langage']):<10} | {radiance_txt}   "
            f"| {f['nb_symboles']:>5} | {oudjat_txt}"
        )
    lignes.append("  " + "-" * 70)

    collisions = carte["collisions"]
    lignes.append("")
    if collisions:
        lignes.append(f"  ⚠ COLLISIONS ({len(collisions)}) — même nom dans plusieurs fichiers :")
        for c in collisions:
            lignes.append(f"    '{c['nom']}' ({c['nb_occurrences']} occurrences, {c['nb_fichiers']} fichiers) :")
            for o in c["occurrences"]:
                lignes.append(
                    f"      → {o['fichier']}:{o['ligne']} (complexité {o['complexite']})"
                )
    else:
        lignes.append("  ✓ Aucune collision de noms détectée.")

    lignes.append("")
    sup = carte["oudjat_supreme"]
    if sup:
        lignes.append(
            f"  👑 OUDJAT SUPRÊME : '{sup['nom']}' dans {sup['fichier']}"
            f" (ligne {sup['ligne']}, complexité {sup['complexite']})"
        )
    elif carte.get("mode") == "rapide":
        lignes.append("  👑 OUDJAT SUPRÊME : non calculé (mode --rapide).")
    else:
        lignes.append("  👑 OUDJAT SUPRÊME : aucun symbole indexé.")

    lignes.append("")
    non_supportes = carte.get("non_supportes") or []
    if non_supportes:
        lignes.append(
            f"  🚫 FICHIERS NON SUPPORTÉS ({len(non_supportes)}) — "
            "aucun analyseur fonctionnel, ignorés de la carte :"
        )
        par_raison = {}
        for ns in non_supportes:
            par_raison.setdefault(ns["raison"], []).append(ns)
        for raison in sorted(par_raison):
            fichiers_r = par_raison[raison]
            lignes.append(f"    · {raison} ({len(fichiers_r)}) :")
            for ns in fichiers_r[:10]:
                lignes.append(f"      → {ns['fichier']} [{ns['extension']}]")
            if len(fichiers_r) > 10:
                lignes.append(f"      … et {len(fichiers_r) - 10} autres.")
    else:
        lignes.append("  ✓ Tous les fichiers rencontrés sont analysables.")
    return "\n".join(lignes)
