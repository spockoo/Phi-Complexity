"""
vigilance.py — Détecteurs ABSOLUS (état unique) pour le GPS mathématique.

Complément du radar (qui est DIFFÉRENTIEL : avant/après) : la vigilance
analyse UN état et signale des faits structurels.

Règle CONSTITUTIONNELLE (comme radar.py, garde testée dans
tests/test_garde_non_prescriptif.py) : l'instrument SIGNALE, il ne
décide jamais. Aucun langage prescriptif (voir LISTE_NOIRE_PRESCRIPTIF
de radar.py). Le tri est humain ; l'instrument ordonne l'attention.

Détecteurs :
- vacuite_hypothese (10) : une hypothèse est syntaxiquement impossible
  (ex. `T < T` requis, `False` supposé). Un théorème sous hypothèse
  impossible est vacuement vrai — le cas réel qui a motivé ce détecteur :
  `BMOControl d d.Tstar` exigeait `d.Tstar < d.Tstar` (2026-10-08).
  Dépliage définitionnel à UN niveau : `F a₁..aₙ` où `F` est un `def`
  du corpus → substitution → recherche de motifs impossibles.
- vacuite_conclusion (9) : la CONCLUSION est syntaxiquement impossible
  (ex. prouver `x < x`). Théorème improuvable (sauf par explosion).
- hypothese_inutilisee (3) : un binder nommé n'apparaît jamais dans le
  corps de la preuve. Hygiène de preuve — signal faible, à trier.

LIMITES EXPLICITES (l'instrument échoue bruyamment sur ce qu'il ne voit pas) :
- Motifs purement SYNTAXIQUES : `t < t`, `t > t`, `t ≠ t`, `False`.
  Ne voit PAS : `x < y ∧ y < x` (transitivité), `n + 1 < n` (arithmétique),
  contradictions via 2+ niveaux de dépliage, raisonnement sémantique.
- Le dépliage est à UN niveau et échoue proprement (hypothèse ignorée,
  pas de faux positif) si le nombre d'arguments ne correspond pas aux
  binders explicites du `def`.
- `hypothese_inutilisee` est un signal faible : tactiques opaques,
  shadowing, ou usage dans le type de retour seul peuvent tromper.
  Jamais un verdict.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .parseur_autonome import parse, tokenize, Token
from .radar import (
    LISTE_NOIRE_PRESCRIPTIF,
    Observation,
    POIDS_SAILLANCE as _POIDS_RADAR,
)

# ────────────────────────────────────────────────────────
# Poids de saillance (fixes, documentés, jamais appris)
# ────────────────────────────────────────────────────────

POIDS_VIGILANCE = {
    "vacuite_hypothese": 10,
    "vacuite_conclusion": 9,
    "hypothese_inutilisee": 3,
}

_LIBELLES = {
    "vacuite_hypothese": "HYPOTHÈSE IMPOSSIBLE",
    "vacuite_conclusion": "CONCLUSION IMPOSSIBLE",
    "hypothese_inutilisee": "HYPOTHÈSE INUTILISÉE (signal faible)",
}

# ────────────────────────────────────────────────────────
# Motifs d'impossibilité syntaxique
# ────────────────────────────────────────────────────────
# On cherche, dans un type normalisé (espaces écrasés) :
#   <terme> <op> <terme>  avec op dans {<, >, ≠, !=} et termes identiques,
# ou le type réduit à "False".
#
# Ne sont PAS impossibles (ne pas signaler) : ≤, ≥, = (réflexifs vrais).

_OPS_IMPOSSIBLES = ("<", ">", "≠", "!=")


def _normaliser(texte: str) -> str:
    """Écrase les espaces pour comparaison syntaxique."""
    return re.sub(r"\s+", "", texte)


def _extraire_comparaison(type_norm: str):
    """Cherche <t> <op> <t> avec op impossible et côtés identiques.

    Heuristique syntaxique : découpe sur l'opérateur de comparaison de
    plus haut niveau (hors parenthèses/crochets). Retourne (gauche, op,
    droite) si les côtés normalisés sont identiques et non vides,
    sinon None.
    """
    # On travaille sur le texte normalisé (sans espaces).
    # Découpe naïve mais prudente : on cherche chaque opérateur et on
    # compare les opérandes adjacentes délimitées par les opérateurs
    # logiques et les parenthèses.
    for op in _OPS_IMPOSSIBLES:
        # Évite de confondre <= avec < : exige que < ne soit pas suivi de =
        if op == "<":
            motif = re.compile(r"(?<![<>=!])<(?![>=])")
        elif op == ">":
            motif = re.compile(r"(?<![<>=!])>(?![>=])")
        else:
            motif = re.compile(re.escape(op))
        for m in motif.finditer(type_norm):
            # Bornes de l'opérande gauche : depuis le dernier séparateur
            # logique ouvrante.
            g_fin = m.start()
            g_deb = g_fin
            prof = 0
            i = g_fin - 1
            while i >= 0:
                c = type_norm[i]
                if c in ")]}⟩⦄›":
                    prof += 1
                elif c in "([{⟨⦃‹":
                    if prof == 0:
                        break
                    prof -= 1
                elif prof == 0 and type_norm.startswith("∧", i):
                    break
                elif prof == 0 and c in "→,":
                    break
                i -= 1
            g_deb = i + 1
            # Bornes de l'opérande droite : jusqu'au prochain séparateur.
            d_deb = m.end()
            d_fin = d_deb
            prof = 0
            j = d_deb
            n = len(type_norm)
            while j < n:
                c = type_norm[j]
                if c in "([{⟨⦃‹":
                    prof += 1
                elif c in ")]}⟩⦄›":
                    if prof == 0:
                        break
                    prof -= 1
                elif prof == 0 and (type_norm.startswith("∧", j)
                                    or c in "→,"):
                    break
                j += 1
            d_fin = j
            gauche = type_norm[g_deb:g_fin]
            droite = type_norm[d_deb:d_fin]
            if gauche and droite and gauche == droite:
                return gauche, op, droite
    return None


def _est_impossible(type_texte: str, conclusion: bool = False) -> str | None:
    """Teste si un type est syntaxiquement impossible.

    Retourne une description du motif trouvé, ou None.
    Ne voit que : `t < t`, `t > t`, `t ≠ t`, et (`False` seul, sauf en
    conclusion : `False` en conclusion est le motif standard de la
    preuve par contradiction, pas une anomalie).
    """
    norm = _normaliser(type_texte)
    if norm == "False" and not conclusion:
        return "type réduit à False"
    trouve = _extraire_comparaison(norm)
    if trouve:
        gauche, op, _ = trouve
        return f"motif `{gauche} {op} {gauche}` (côtés identiques)"
    return None


# ────────────────────────────────────────────────────────
# Dépliage définitionnel à un niveau
# ────────────────────────────────────────────────────────

def _carte_definitions(decls) -> dict:
    """Construit {nom_def: (binders_explicites, corps)} pour les `def`.

    binders_explicites : noms des binders NON implicites, dans l'ordre.
    """
    carte = {}
    for d in decls:
        if d.kind != "def" or not d.corps:
            continue
        explicites = [nom for nom, implicite in (d.binders or [])
                      if not implicite]
        carte[d.nom] = (explicites, d.corps)
    return carte


def _decouper_arguments(toks, debut: int, n_attendus: int):
    """Découpe les n_attendus arguments après la tête (index debut).

    Heuristique : chaque argument est un atome de niveau 0 (les chaînes
    pointées `a.b.c` et les groupes équilibrés restent groupés).
    Retourne la liste des textes d'arguments, ou None si le découpage
    est ambigu (nombre d'atomes ≠ n_attendus).
    """
    OUVRANTS = set("([{⟨⦃‹")
    FERMANTS = set(")]}⟩⦄›")
    args = []
    courant = []
    prof = 0
    prev_ete_point = False
    i = debut
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.type == "nl":
            i += 1
            continue
        txt = t.text
        est_point = (t.type == "op" and txt == ".")
        if txt in OUVRANTS:
            prof += 1
            courant.append(txt)
        elif txt in FERMANTS:
            prof -= 1
            courant.append(txt)
        elif prof == 0 and not est_point and not prev_ete_point:
            # Nouvel atome de niveau 0 → nouvel argument (sauf le 1er).
            if courant:
                args.append("".join(courant))
                courant = []
            courant.append(txt)
        else:
            courant.append(txt)
        prev_ete_point = est_point
        i += 1
    if courant:
        args.append("".join(courant))
    if len(args) != n_attendus:
        return None
    return args


def _substituer(corps: str, binders: list, args: list) -> str:
    """Substitution SIMULTANÉE (un seul passage) des binders par les args.

    Évite le re-appariement (ex. substituer `d` dans le `d.Tstar` déjà
    substitué). Les noms sont protégés par des frontières qui excluent
    les chaînes pointées (`d` ne matche pas dans `d.Tstar`).
    """
    if not binders or len(binders) != len(args):
        return corps
    # Plus longs d'abord (préfixes) — avec l'alternance unique, un seul
    # passage suffit de toute façon.
    ordre = sorted(range(len(binders)), key=lambda i: -len(binders[i]))
    motif = re.compile(
        r"(?<![\w.])("
        + "|".join(re.escape(binders[i]) for i in ordre)
        + r")(?![\w.])"
    )
    table = {binders[i]: args[i] for i in range(len(binders))}
    return motif.sub(lambda m: table[m.group(1)], corps)


def _tenter_depliage(type_texte: str, carte_defs: dict) -> str | None:
    """Tente un dépliage à un niveau de `F a₁..aₙ` (F un def du corpus).

    Retourne le corps substitué, ou None si impossible/ambigu.
    """
    toks, _, _ = tokenize(type_texte)
    # Filtre les tokens significatifs (tête = premier identifiant).
    sig = [t for t in toks if t.type != "nl"]
    if not sig or sig[0].type != "ident":
        return None
    nom = sig[0].text
    if nom not in carte_defs:
        return None
    binders, corps = carte_defs[nom]
    if not binders:
        return None
    args = _decouper_arguments(sig, 1, len(binders))
    if args is None:
        return None
    return _substituer(corps, binders, args)


def _tester_vacuite(type_texte: str, carte_defs: dict,
                    conclusion: bool = False) -> str | None:
    """Teste la vacuité d'un type : direct, puis après dépliage à 1 niveau.

    Retourne la description du motif, ou None.
    """
    direct = _est_impossible(type_texte, conclusion=conclusion)
    if direct:
        return direct
    deplie = _tenter_depliage(type_texte, carte_defs)
    if deplie:
        trouve = _est_impossible(deplie, conclusion=conclusion)
        if trouve:
            return trouve + " (après dépliage à un niveau)"
    return None


# ────────────────────────────────────────────────────────
# Extraction des hypothèses (binders typés de l'énoncé)
# ────────────────────────────────────────────────────────

def _enonce(decl) -> str:
    """L'en-tête de la déclaration (tout ce qui précède `:=`)."""
    texte = decl.texte
    corps = decl.corps or ""
    if corps and texte.endswith(corps):
        tete = texte[: -len(corps)]
    else:
        tete = texte
    tete = tete.rstrip()
    if tete.endswith(":="):
        tete = tete[:-2].rstrip()
    return tete


def _extraire_groupes_hypotheses(enonce: str) -> list:
    """Extrait les groupes de binders `(n1 n2 : Type)` de l'énoncé.

    Retourne [(noms, type)] où noms est la liste des noms liés dans le
    groupe et type le texte du type ("" si non typé).
    """
    toks, _, _ = tokenize(enonce)
    groupes = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.text == "(" and t.type == "op":
            prof = 1
            j = i + 1
            pos_deux_points = -1
            while j < n and prof > 0:
                tj = toks[j].text
                if tj in "([{":
                    prof += 1
                elif tj in ")]}":
                    prof -= 1
                    if prof == 0:
                        break
                elif prof == 1 and tj == ":" and toks[j].type == "op":
                    nxt = toks[j + 1].text if j + 1 < n else ""
                    if nxt not in (":", "="):
                        pos_deux_points = j
                j += 1
            if prof == 0:
                if pos_deux_points != -1:
                    noms = [tok.text for tok in toks[i + 1:pos_deux_points]
                            if tok.type == "ident"]
                    morceaux = [tok.text for tok in toks[pos_deux_points + 1:j]
                                if tok.type != "nl"]
                    type_txt = " ".join(morceaux)
                    type_txt = re.sub(r"\s*\.\s*", ".", type_txt).strip()
                else:
                    noms = [tok.text for tok in toks[i + 1:j]
                            if tok.type == "ident"]
                    type_txt = ""
                if noms:
                    groupes.append((noms, type_txt))
            i = j + 1 if prof == 0 else i + 1
        else:
            i += 1
    return groupes


def _extraire_types_hypotheses(enonce: str) -> list:
    """Extrait les types des binders explicites `(nom : Type)` de l'énoncé.

    Retourne la liste des textes de types (sans les noms).
    """
    return [t for _noms, t in _extraire_groupes_hypotheses(enonce) if t]


# ────────────────────────────────────────────────────────
# Détecteurs
# ────────────────────────────────────────────────────────

def detecter_vacuite(fiches: dict, decls_par_fichier: dict) -> list:
    """Détecte les hypothèses et conclusions syntaxiquement impossibles.

    fiches : {(fichier, nom): Fiche} (radar.scanner).
    decls_par_fichier : {fichier: [DeclAutonome]} pour le dépliage.

    Retourne des Observation (kind vacuite_hypothese / vacuite_conclusion).
    Ne lève jamais : un fichier illisible est ignoré.
    """
    observations = []
    # Carte des définitions (tous fichiers confondus).
    carte_defs = {}
    for decls in decls_par_fichier.values():
        carte_defs.update(_carte_definitions(decls))

    for (fichier, nom), fiche in sorted(fiches.items()):
        decls = decls_par_fichier.get(fichier, [])
        decl = next((d for d in decls if d.nom == nom), None)
        if decl is None:
            continue
        enonce = _enonce(decl)
        # 1. Hypothèses (binders typés).
        for type_h in _extraire_types_hypotheses(enonce):
            motif = _tester_vacuite(type_h, carte_defs)
            if motif:
                observations.append(Observation(
                    kind="vacuite_hypothese",
                    fichier=fiche.fichier,
                    ligne=fiche.ligne,
                    nom=fiche.nom,
                    saillance=POIDS_VIGILANCE["vacuite_hypothese"],
                    avant="",
                    apres=f"hypothèse impossible : {motif} "
                          f"(type : «{type_h[:120]}»)",
                ))
                break  # un signal par déclaration suffit
        # 2. Conclusion (type de retour). `False` y est légitime
        # (preuve par contradiction) : on ne signale que les motifs
        # t < t style.
        if decl.type_retour:
            motif = _tester_vacuite(decl.type_retour, carte_defs,
                                   conclusion=True)
            if motif:
                observations.append(Observation(
                    kind="vacuite_conclusion",
                    fichier=fiche.fichier,
                    ligne=fiche.ligne,
                    nom=fiche.nom,
                    saillance=POIDS_VIGILANCE["vacuite_conclusion"],
                    avant="",
                    apres=f"conclusion impossible : {motif} "
                          f"(type : «{decl.type_retour[:120]}»)",
                ))
    observations.sort(key=lambda o: (-o.saillance, o.nom))
    return observations


def detecter_hypotheses_inutilisees(fiches: dict,
                                    decls_par_fichier: dict) -> list:
    """Signale les binders nommés jamais référencés dans le corps.

    Signal FAIBLE (saillance 3) : tactiques opaques, shadowing et usage
    dans le type seul peuvent tromper. À trier humainement.
    """
    observations = []
    for (fichier, nom), fiche in sorted(fiches.items()):
        decls = decls_par_fichier.get(fichier, [])
        decl = next((d for d in decls if d.nom == nom), None)
        if decl is None or not decl.corps or not decl.binders:
            continue
        enonce = _enonce(decl)
        groupes = _extraire_groupes_hypotheses(enonce)
        for nom_binder, _implicite in decl.binders:
            if not nom_binder or nom_binder == "_":
                continue
            # Contexte d'usage : corps + conclusion + types des AUTRES
            # binders (le type propre ne compte pas : un binder ne
            # s'utilise pas lui-même). Un binder présent dans le type
            # d'un autre ou la conclusion est légitime.
            autres_types = [t for noms, t in groupes
                            if nom_binder not in noms and t]
            contexte = (decl.corps + " " + (decl.type_retour or "")
                        + " " + " ".join(autres_types))
            toks_ctx, _, _ = tokenize(contexte)
            utilises = {t.text for t in toks_ctx if t.type == "ident"}
            if nom_binder not in utilises:
                observations.append(Observation(
                    kind="hypothese_inutilisee",
                    fichier=fiche.fichier,
                    ligne=fiche.ligne,
                    nom=fiche.nom,
                    saillance=POIDS_VIGILANCE["hypothese_inutilisee"],
                    avant="",
                    apres=f"binder «{nom_binder}» absent du corps, de la "
                          f"conclusion et des autres types (signal faible)",
                ))
    observations.sort(key=lambda o: (o.nom, o.apres))
    return observations


# ────────────────────────────────────────────────────────
# Inventaire des axiomes
# ────────────────────────────────────────────────────────

@dataclass
class AxiomeInfo:
    """Un axiome déclaré dans le corpus."""
    fichier: str
    ligne: int
    nom: str
    enonce_court: str


def inventaire_axiomes(fiches: dict) -> list:
    """Liste tous les axiomes déclarés (kind == 'axiom').

    Inventaire, pas anomalie : pas de saillance, pas de jugement.
    """
    resultats = []
    for (fichier, nom), fiche in sorted(fiches.items()):
        if fiche.kind == "axiom" or fiche.est_axiom:
            resultats.append(AxiomeInfo(
                fichier=fiche.fichier,
                ligne=fiche.ligne,
                nom=fiche.nom,
                enonce_court=fiche.enonce_court,
            ))
    return resultats


# ────────────────────────────────────────────────────────
# Scan + formatage
# ────────────────────────────────────────────────────────

def scanner_complet(racine: str):
    """Parse un état → (fiches, decls_par_fichier).

    fiches : {(fichier, nom): Fiche} (même modèle que radar.scanner).
    decls_par_fichier : {fichier: [DeclAutonome]} (pour le dépliage).
    Ne lève jamais sur un fichier illisible.
    """
    from .radar import _fiche, _fichiers_lean
    import os
    fiches = {}
    decls_par_fichier = {}
    base = racine if os.path.isdir(racine) else os.path.dirname(racine) or "."
    for chemin in _fichiers_lean(racine):
        try:
            with open(chemin, encoding="utf-8") as fh:
                code = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(chemin, base)
        resultat = parse(code)
        decls_par_fichier[rel] = resultat.declarations
        for decl in resultat.declarations:
            fiches[(rel, decl.nom)] = _fiche(rel, decl)
    return fiches, decls_par_fichier


def _garde_prescriptive(texte: str) -> bool:
    """Vrai si le texte contient du langage prescriptif interdit."""
    bas = texte.lower()
    return any(p in bas for p in LISTE_NOIRE_PRESCRIPTIF)


def formater_console(observations: list, axiomes: list) -> str:
    """Rendu console : faits uniquement, aucun langage prescriptif."""
    lignes = [f"🔍 VIGILANCE — {len(observations)} observations, "
              f"{len(axiomes)} axiome(s) déclaré(s)"]
    for o in observations:
        libelle = _LIBELLES.get(o.kind, o.kind)
        lignes.append(f"[{o.saillance:2d}] {libelle}  {o.fichier}:{o.ligne}"
                      f"  {o.nom}")
        if o.apres:
            lignes.append(f"     {o.apres}")
    if axiomes:
        lignes.append("")
        lignes.append("AXIOMES DÉCLARÉS :")
        for a in axiomes:
            lignes.append(f"  {a.fichier}:{a.ligne}  axiom {a.nom}"
                          f"  «{a.enonce_court[:100]}»")
    return "\n".join(lignes)


def vers_dict(observations: list, axiomes: list) -> dict:
    """Sérialisation JSON : observations + inventaire."""
    return {
        "observations": [
            {"kind": o.kind, "fichier": o.fichier, "ligne": o.ligne,
             "nom": o.nom, "saillance": o.saillance, "detail": o.apres}
            for o in observations],
        "axiomes": [
            {"fichier": a.fichier, "ligne": a.ligne, "nom": a.nom,
             "enonce": a.enonce_court}
            for a in axiomes],
    }
