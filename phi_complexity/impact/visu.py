# -*- coding: utf-8 -*-
"""
visu.py -- Visualisation HTML autonome de l'analyse d'impact.

Genere un fichier HTML SANS dependance externe (pas de CDN, pas de JS
externe) : le graphe d'impact est rendu en SVG construit directement en
Python. Principe d'autonomie.

API:
    generer_html(impact, risques, graphe_json, chemin_sortie) -> str

Mission RUCHE-IMPACT-ANALYSIS, chantier 4/5 : VISUALISATION.
Ne pas commiter, ne pas pusher.
"""

from __future__ import annotations

import html
import math
import os

# ---------------------------------------------------------------------------
# Constantes de rendu
# ---------------------------------------------------------------------------

COULEURS_NIVEAU = {
    "FAIBLE": "#2ca02c",
    "MOYEN": "#ffbd0a",
    "ELEVE": "#ff7f0e",
    "CRITIQUE": "#d62728",
}
COULEUR_SOURCE = "#1f77b4"   # nœud modifie (source), toujours bleu
COULEUR_INCONNU = "#7f7f7f"  # niveau de risque absent

MAX_NŒUDS_AFFICHES = 80       # tronque au-dela, compteur "+N autres"

# Geometrie des nœuds
LARGEUR_NŒUD = 230
HAUTEUR_NŒUD = 44
MARGE_X = 40
LARGEUR_COLONNE = 300        # distance horizontale entre colonnes de profondeur
ESPACEMENT_Y = 66            # espacement vertical entre nœuds d'une meme colonne
MARGE_Y = 90
RAYON = 8

# Hauteur de la zone reservee au bandeau de resume + legende (en-tete HTML).
EN_TETE_HTML_PX = 170


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _echapper(s) -> str:
    """Echappe une chaine pour inclusion en HTML/SVG."""
    return html.escape(str(s), quote=True)


def _nom_court(identifiant: str, longueur_max: int = 26) -> str:
    """Nom affiche dans le nœud : dernier composant point, tronque."""
    nom = str(identifiant).rsplit(".", 1)[-1]
    if len(nom) > longueur_max:
        nom = nom[: longueur_max - 1] + "…"
    return nom


def _couleur_noeud(est_source: bool, niveau: str | None) -> str:
    if est_source:
        return COULEUR_SOURCE
    return COULEURS_NIVEAU.get((niveau or "").upper(), COULEUR_INCONNU)


def _texte_clair_sur_fond(couleur_hex: str) -> bool:
    """Decide si le texte doit etre blanc (fond sombre) ou noir (fond clair)."""
    c = couleur_hex.lstrip("#")
    if len(c) != 6:
        return True
    r, g, b = (int(c[i: i + 2], 16) for i in (0, 2, 4))
    # Luminance relative approchee (sRGB).
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return lum < 0.55


# ---------------------------------------------------------------------------
# Construction du modele de rendu
# ---------------------------------------------------------------------------

def _construire_profondeurs(impact: dict) -> tuple[str, dict[str, int], dict[str, list[str]]]:
    """
    Renvoie (source, profondeur_par_id, chemin_par_id).
    La source est en profondeur 0.
    """
    source = str(impact.get("noeud", ""))
    profondeurs: dict[str, int] = {source: 0}
    chemins: dict[str, list[str]] = {source: [source]}
    for entree in impact.get("impactes", []) or []:
        nid = str(entree.get("id", ""))
        if not nid or nid == source:
            continue
        try:
            prof = int(entree.get("profondeur", 1))
        except (TypeError, ValueError):
            prof = 1
        if prof < 1:
            prof = 1
        # Garde la profondeur minimale rencontree (plus proche de la source).
        if nid not in profondeurs or prof < profondeurs[nid]:
            profondeurs[nid] = prof
            chemins[nid] = [str(x) for x in (entree.get("chemin") or [])]
    return source, profondeurs, chemins


def _selectionner_noeuds(
    source: str,
    profondeurs: dict[str, int],
) -> tuple[list[str], int]:
    """
    Ordonne les nœuds (source d'abord, puis profondeur croissante, puis id)
    et tronque a MAX_NŒUDS_AFFICHES. Renvoie (ids_affiches, nb_tronques).
    """
    ordre = sorted(
        profondeurs,
        key=lambda nid: (0 if nid == source else 1, profondeurs[nid], nid),
    )
    tronques = max(0, len(ordre) - MAX_NŒUDS_AFFICHES)
    return ordre[:MAX_NŒUDS_AFFICHES], tronques


def _disposer(
    ids_affiches: list[str],
    profondeurs: dict[str, int],
) -> tuple[dict[str, tuple[float, float]], int, int]:
    """
    Layout en colonnes par profondeur : x = f(profondeur), y = empilement
    centre dans la colonne. Renvoie (positions, largeur_svg, hauteur_svg).
    """
    colonnes: dict[int, list[str]] = {}
    for nid in ids_affiches:
        colonnes.setdefault(profondeurs[nid], []).append(nid)

    nb_colonnes = (max(colonnes) + 1) if colonnes else 1
    largeur_svg = MARGE_X * 2 + nb_colonnes * LARGEUR_COLONNE
    hauteur_max_colonne = max(
        (MARGE_Y * 2 + (len(ids) - 1) * ESPACEMENT_Y + HAUTEUR_NŒUD)
        for ids in colonnes.values()
    )
    hauteur_svg = max(hauteur_max_colonne, 260)

    positions: dict[str, tuple[float, float]] = {}
    for prof, ids in colonnes.items():
        hauteur_colonne = (len(ids) - 1) * ESPACEMENT_Y + HAUTEUR_NŒUD
        y_debut = (hauteur_svg - hauteur_colonne) / 2.0
        x = MARGE_X + prof * LARGEUR_COLONNE
        for i, nid in enumerate(ids):
            positions[nid] = (x, y_debut + i * ESPACEMENT_Y)
    return positions, largeur_svg, hauteur_svg


# ---------------------------------------------------------------------------
# Rendu SVG
# ---------------------------------------------------------------------------

def _svg_noeud(
    morceaux: list[str],
    x: float,
    y: float,
    nid: str,
    score: float | None,
    niveau: str | None,
    est_source: bool,
    chemin: list[str],
) -> None:
    couleur = _couleur_noeud(est_source, niveau)
    texte_blanc = _texte_clair_sur_fond(couleur)
    couleur_texte = "#ffffff" if texte_blanc else "#111111"
    couleur_texte_2 = "#f0f0f0" if texte_blanc else "#333333"

    if est_source:
        ligne2 = "SOURCE" + (f" — score {score:.2f}" if score is not None else "")
    else:
        ligne2 = f"score {score:.2f}" if score is not None else "score n/a"
        if niveau:
            ligne2 += f" · {niveau}"

    infobulle_parts = [f"id : {nid}"]
    if chemin and len(chemin) > 1:
        infobulle_parts.append("chemin : " + " → ".join(chemin))
    infobulle = _echapper(" | ".join(infobulle_parts))

    morceaux.append(f'<g class="noeud"><title>{infobulle}</title>')
    morceaux.append(
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{LARGEUR_NŒUD}" '
        f'height="{HAUTEUR_NŒUD}" rx="{RAYON}" fill="{couleur}" '
        f'stroke="#222" stroke-width="1.2"/>'
    )
    morceaux.append(
        f'<text x="{x + LARGEUR_NŒUD / 2:.1f}" y="{y + 19:.1f}" '
        f'text-anchor="middle" font-family="sans-serif" font-size="13" '
        f'font-weight="bold" fill="{couleur_texte}">'
        f'{_echapper(_nom_court(nid))}</text>'
    )
    morceaux.append(
        f'<text x="{x + LARGEUR_NŒUD / 2:.1f}" y="{y + 35:.1f}" '
        f'text-anchor="middle" font-family="sans-serif" font-size="11" '
        f'fill="{couleur_texte_2}">{_echapper(ligne2)}</text>'
    )
    morceaux.append("</g>")


def _svg_arete(
    morceaux: list[str],
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    couleur: str = "#9a9a9a",
) -> None:
    """Arete courbe (Bezier cubique) entre bord droit du parent et bord gauche."""
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        return
    decalage = max(30.0, (x2 - x1) / 2.0)
    morceaux.append(
        f'<path d="M {x1:.1f} {y1:.1f} '
        f'C {x1 + decalage:.1f} {y1:.1f}, {x2 - decalage:.1f} {y2:.1f}, '
        f'{x2:.1f} {y2:.1f}" fill="none" stroke="{couleur}" '
        f'stroke-width="1.4" opacity="0.55" marker-end="url(#fleche)"/>'
    )


def _construire_svg(
    source: str,
    ids_affiches: list[str],
    profondeurs: dict[str, int],
    risques: dict[str, dict],
    graphe_json: dict,
    chemins: dict[str, list[str]],
) -> str:
    positions, largeur, hauteur = _disposer(ids_affiches, profondeurs)
    ensemble = set(ids_affiches)
    morceaux: list[str] = []
    morceaux.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largeur}" '
        f'height="{hauteur}" viewBox="0 0 {largeur} {hauteur}" '
        f'role="img" aria-label="Graphe d\'impact">'
    )
    # Marqueur de fleche reutilise par les aretes.
    morceaux.append(
        '<defs><marker id="fleche" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#9a9a9a"/></marker></defs>'
    )

    # Aretes d'abord (sous les nœuds).
    aretes = (graphe_json or {}).get("aretes", []) or []
    for arete in aretes:
        src = str(arete.get("src", ""))
        dst = str(arete.get("dst", ""))
        if src not in ensemble or dst not in ensemble:
            continue
        if profondeurs.get(src, 0) >= profondeurs.get(dst, 0) and src != dst:
            # N'affiche que les aretes qui vont vers l'avant (profondeur croissante).
            continue
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        _svg_arete(
            morceaux,
            x1 + LARGEUR_NŒUD,
            y1 + HAUTEUR_NŒUD / 2.0,
            x2,
            y2 + HAUTEUR_NŒUD / 2.0,
        )

    # Nœuds.
    for nid in ids_affiches:
        x, y = positions[nid]
        info = risques.get(nid, {}) if isinstance(risques, dict) else {}
        score = info.get("score")
        niveau = info.get("niveau")
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        _svg_noeud(
            morceaux, x, y, nid, score_f, niveau,
            est_source=(nid == source),
            chemin=chemins.get(nid, [nid]),
        )
    morceaux.append("</svg>")
    return "\n".join(morceaux)


# ---------------------------------------------------------------------------
# Rendu HTML
# ---------------------------------------------------------------------------

def _legende_html() -> str:
    pastilles = []
    for niveau, couleur in COULEURS_NIVEAU.items():
        pastilles.append(
            f'<span class="pastille" style="background:{couleur}"></span> {niveau}'
        )
    pastilles.append(
        f'<span class="pastille" style="background:{COULEUR_SOURCE}"></span> '
        "NŒUD SOURCE"
    )
    return " &nbsp; ".join(pastilles)


def generer_html(
    impact: dict,
    risques: dict[str, dict],
    graphe_json: dict,
    chemin_sortie: str,
) -> str:
    """
    Genere un fichier HTML autonome visualisant l'analyse d'impact.

    impact      : resultat de propagation.impact_avant ->
                  {"noeud", "impactes":[{"id","profondeur","chemin"}], ...}
    risques     : {nid: {"score": float, "niveau": "FAIBLE"|"MOYEN"|"ELEVE"|"CRITIQUE"}}
    graphe_json : {"noeuds":[...], "aretes":[{"src","dst","type"}]}
    chemin_sortie : chemin du fichier HTML a ecrire.

    Retourne le chemin du fichier ecrit.
    """
    impact = impact or {}
    risques = risques or {}
    graphe_json = graphe_json or {}

    source, profondeurs, chemins = _construire_profondeurs(impact)
    ids_affiches, nb_tronques = _selectionner_noeuds(source, profondeurs)

    nb_impactes = max(0, len(profondeurs) - 1)
    profondeur_max = max(profondeurs.values()) if profondeurs else 0
    info_source = risques.get(source, {})
    score_source = info_source.get("score")

    svg = _construire_svg(source, ids_affiches, profondeurs, risques,
                          graphe_json, chemins)

    total_noeuds_graphe = len((graphe_json.get("noeuds") or []))
    total_aretes = len((graphe_json.get("aretes") or []))

    resume = (
        f"<b>Nœud modifie :</b> {_echapper(source)} &nbsp;|&nbsp; "
        f"<b>Impactes :</b> {nb_impactes} &nbsp;|&nbsp; "
        f"<b>Profondeur max :</b> {profondeur_max} &nbsp;|&nbsp; "
    )
    if score_source is not None:
        try:
            resume += f"<b>Score source :</b> {float(score_source):.2f} &nbsp;|&nbsp; "
        except (TypeError, ValueError):
            pass
    resume += (
        f"<b>Graphe :</b> {total_noeuds_graphe} nœuds / {total_aretes} aretes"
    )
    if nb_tronques:
        resume += (
            f'<br><span class="avertissement">Affichage limite a '
            f"{MAX_NŒUDS_AFFICHES} nœuds : <b>+{nb_tronques} autres</b> "
            "non dessines.</span>"
        )

    page = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analyse d'impact — {_echapper(_nom_court(source))}</title>
<style>
  body {{ font-family: sans-serif; margin: 0; background: #f7f7f7; color: #222; }}
  header {{ background: #1a1a2e; color: #eee; padding: 18px 24px; }}
  header h1 {{ margin: 0 0 8px 0; font-size: 20px; }}
  header .resume {{ font-size: 14px; line-height: 1.7; }}
  .avertissement {{ color: #ffbd0a; }}
  .legende {{ background: #fff; border-bottom: 1px solid #ddd;
             padding: 10px 24px; font-size: 13px; }}
  .pastille {{ display: inline-block; width: 14px; height: 14px;
              border-radius: 3px; vertical-align: -2px; margin-left: 10px;
              border: 1px solid #555; }}
  .pastille:first-child {{ margin-left: 0; }}
  main {{ padding: 16px 24px; overflow-x: auto; }}
  svg {{ background: #ffffff; border: 1px solid #ddd; border-radius: 6px; }}
  footer {{ padding: 10px 24px; font-size: 12px; color: #777; }}
</style>
</head>
<body>
<header>
  <h1>Analyse d'impact — propagation depuis le nœud modifie</h1>
  <div class="resume">{resume}</div>
</header>
<div class="legende"><b>Legende :</b> {_legende_html()}</div>
<main>
{svg}
</main>
<footer>Genere par impact_analyse.visu — SVG autonome, zero dependance externe.</footer>
</body>
</html>
"""

    dossier = os.path.dirname(os.path.abspath(chemin_sortie))
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    with open(chemin_sortie, "w", encoding="utf-8") as f:
        f.write(page)
    return chemin_sortie


# ---------------------------------------------------------------------------
# Test manuel rapide : python3 visu.py
# ---------------------------------------------------------------------------

def _impact_jouet(nb_noeuds: int = 20, niveaux: int = 3) -> tuple[dict, dict, dict]:
    """Construit un impact jouet : chaine ramifiee sur `niveaux` profondeurs."""
    noeuds = [{"id": "mod.source"}]
    aretes = []
    impactes = []
    risques: dict[str, dict] = {"mod.source": {"score": 0.95, "niveau": "CRITIQUE"}}
    cycle_niveaux = ["FAIBLE", "MOYEN", "ELEVE", "CRITIQUE"]
    scores = {"FAIBLE": 0.15, "MOYEN": 0.40, "ELEVE": 0.65, "CRITIQUE": 0.90}
    par_profondeur: dict[int, list[str]] = {0: ["mod.source"]}
    compteur = 0
    for prof in range(1, niveaux + 1):
        par_profondeur[prof] = []
        for parent in par_profondeur[prof - 1]:
            for _b in range(2):
                if compteur >= nb_noeuds - 1:
                    break
                compteur += 1
                nid = f"pkg.module{compteur}.fonction{compteur}"
                noeuds.append({"id": nid})
                aretes.append({"src": parent, "dst": nid, "type": "appel"})
                niveau = cycle_niveaux[(compteur + prof) % len(cycle_niveaux)]
                impactes.append(
                    {"id": nid, "profondeur": prof,
                     "chemin": ["mod.source", nid]})
                risques[nid] = {"score": scores[niveau], "niveau": niveau}
                par_profondeur[prof].append(nid)
            if compteur >= nb_noeuds - 1:
                break
    impact = {"noeud": "mod.source", "impactes": impactes}
    graphe = {"noeuds": noeuds, "aretes": aretes}
    return impact, risques, graphe


if __name__ == "__main__":
    imp, ris, gra = _impact_jouet(20, 3)
    sortie = "/tmp/test_visu_impact.html"
    generer_html(imp, ris, gra, sortie)
    taille = os.path.getsize(sortie)
    print(f"Ecrit : {sortie} ({taille} octets)")
