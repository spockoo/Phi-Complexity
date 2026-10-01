"""phi_complexity/chemins_verifiables.py — Chemins vérifiables vers un sorry.

Un « chemin vérifiable », c'est un candidat de câblage IDENTIFIÉ FORMELLEMENT
comme une preuve : un fichier Lean que Lean lui-même tranche. L'instrument
propose, Lean dispose :

- PROUVÉ   : le fichier compile (exit 0, aucun sorry généré) ;
- RÉFUTÉ   : Lean refuse, et son diagnostic nomme formellement ce qui manque
             (buts non résolus avec leurs types) ;
- INDÉCIDÉ : timeout — ni preuve ni réfutation, dit honnêtement.

Portée honnête (v1) :
- candidats mono-source : un théorème/lemme PROUVÉ (jamais une déclaration
  contenant un sorry) proposé comme ingrédient principal ;
- classement GUIDÉ et expliqué — preuve typée directe d'abord
  (tête logique pondérée par rareté, recouvrement de types pondéré par
  rareté, lieurs typés comme ceux du sorry), proximité de module comme
  a priori faible (même fichier, clôture d'imports, chantiers du registre)
  — pas d'explosion combinatoire
  (R4) : au plus `max_candidats` candidats, un squelette par candidat ;
- les `def` ne sont pas des candidats : une définition n'est pas formellement
  identifiée comme une preuve.

Portée honnête (v2, multi-lemmes — formulation validée 2026-10-01) :
- `chemins()` énumère des chemins P = (l_1, …, l_n) par chaînage avant GUIDÉ
  (chaque étape consomme la précédente), borné par l'INÉGALITÉ DE BUDGET
  C(P) ≤ B(S) — relative (aucune constante absolue), étalonnée sur le pool,
  vérifiée PENDANT la génération (un préfixe hors budget n'est jamais étendu) ;
- fragment D : squelettes totalement explicites (`have` typés + `exact` /
  `refine`, trous `?_` remplis par unification depuis les types explicites) —
  le générateur n'émet JAMAIS de tactique de recherche ; Lean vérifie,
  il ne cherche pas ;
- ensemble admissible vide → INDÉCIDÉ A PRIORI, sans brûler un build Lean ;
- chaque soumission à Lean archive sa preuve d'admissibilité (R4 comme
  propriété testée, pas comme règle de conduite) ;
- division du travail : `candidats_cablage()` reste la piste d'attention
  (large : tout score > 0) ; `chemins()` ne retient que des chemins VERS S —
  la conclusion doit se connecter au but, longueur 1 comprise.
"""
from __future__ import annotations

import bisect
import heapq
import itertools
import math
import os
import re
import statistics
import subprocess
from dataclasses import dataclass, field


@dataclass
class DeclarationProuvee:
    module: str
    fichier: str
    nom: str
    genre: str            # "theorem" | "lemma"
    ligne: int
    entete: str           # texte de l'en-tête (jusqu'à :=)
    lieurs: list          # groupes de lieurs EXPLICITES, ex. ["(ν : ℝ)", ...]
    noms_lieurs: list     # noms seuls, ex. ["ν", "hν", "data"]
    conclusion: str
    a_sorry: bool = False
    #: Nom fully-qualified (namespaces englobants) pour l'émission Lean.
    #: `nom` reste le nom court (affichage, raisons, JSON).
    nom_qualifie: str = ""
    #: Commandes `open` au niveau du fichier du module source : le
    #: squelette les rejoue pour que les identifiants courts résolvent
    #: comme dans le module d'origine.
    opens: list = field(default_factory=list)


def _nom_lean(d: DeclarationProuvee) -> str:
    """Nom à émettre dans le code Lean : fully-qualified quand connu."""
    return d.nom_qualifie or d.nom


@dataclass
class Candidat:
    sorry: str
    module_source: str
    fichier_source: str
    declaration: str
    ligne: int
    entete_source: str
    conclusion_source: str
    squelette: str
    score: float
    raisons: list = field(default_factory=list)
    #: Mesure des directions par trou (trois zones ATTEIGNABLE / IMPOSSIBLE /
    #: INCONNU) — annotation seulement, aucun élagage avant validation de
    #: solidité par Lean (docs/DISCIPLINE_ATTEIGNABILITE_TYPEE.md).
    directions: dict = field(default_factory=dict)
    #: Directions prédites IMPOSSIBLE : [{"trou", "type_trou", "terme",
    #: "raison"}] — pour la validation de solidité (`--valider-solidite`).
    impossibles: list = field(default_factory=list)


def module_depuis_fichier(dossier: str, chemin: str) -> str:
    rel = os.path.relpath(chemin, dossier)
    if rel.endswith(".lean"):
        rel = rel[:-5]
    return rel.replace(os.sep, ".")


def _lignes_decapees(chemin: str):
    from phi_complexity.piste_sorry import decaper_lean
    with open(chemin, encoding="utf-8") as fh:
        return decaper_lean(fh.read())


def extraire_entete(nom: str, texte: str) -> str | None:
    """En-tête d'une déclaration : du mot-clé jusqu'au `:=`/`=>`/`where`
    de profondeur 0.

    Un `:=` de valeur par défaut `(x : ℕ := 0)` ne coupe jamais l'en-tête :
    seule la profondeur réelle tranche.
    """
    m = re.search(r"(?:theorem|lemma)\s+" + re.escape(nom) + r"\b", texte)
    if not m:
        return None
    debut = m.start()
    prof = 0
    i, n = m.end(), len(texte)
    while i < n:
        c = texte[i]
        if c in "([{":
            prof += 1
        elif c in ")]}":
            prof = max(0, prof - 1)
        elif prof == 0:
            if texte.startswith(":=", i) or texte.startswith("=>", i):
                return texte[debut:i].strip()
            if re.match(r"where\b", texte[i:]):
                return texte[debut:i].strip()
        i += 1
    return texte[debut:].strip()


def _profondeur_nulle_deux_points(entete: str, debut: int) -> int:
    """Position du `:` qui sépare les lieurs de la conclusion (profondeur 0)."""
    prof = 0
    for i in range(debut, len(entete)):
        c = entete[i]
        if c in "([{":
            prof += 1
        elif c in ")]}":
            prof = max(0, prof - 1)
        elif c == ":" and prof == 0:
            # éviter ":=" et les "::"
            if entete[i + 1:i + 2] not in ("=", ":"):
                return i
    return -1


def _noms_groupe(contenu: str) -> list:
    """Noms liés par un groupe `(a b : T)` : avant le premier `:` de
    profondeur 0. Un groupe sans `:` (type nu) ne lie aucun nom."""
    prof = 0
    for i, c in enumerate(contenu):
        if c in "([{":
            prof += 1
        elif c in ")]}":
            prof = max(0, prof - 1)
        elif c == ":" and prof == 0:
            return re.findall(_IDENTIFIANT, contenu[:i])
    return []


def analyser_entete(entete: str, nom: str) -> tuple[list, list, str]:
    """Retourne (groupes explicites, noms des lieurs explicites, conclusion).

    Analyse à profondeur réelle (pile) :
    - les parenthèses imbriquées dans les types — p. ex.
      `(hE0 : fam.E0 ^ 2 = kineticEnergy (fun x _ => data.u0 x) 0)` —
      ne produisent jamais de faux lieurs (`fun`, `x`, `data`... ne sont
      pas des lieurs) ;
    - les groupes `{...}` (implicites) et `[...]` (instance) sont exclus
      des lieurs positionnels : Lean les remplit seul par unification /
      synthèse de classes. Les passer positionnellement au `refine`
      décale tous les arguments (diagnostiqué sur `leray_hopf_conditional`) ;
    - la conclusion (après le premier `:` de profondeur 0) n'est jamais
      analysée comme des lieurs.
    """
    m = re.search(r"(?:theorem|lemma)\s+" + re.escape(nom) + r"\b", entete)
    if not m:
        return [], [], ""
    pos_deux = _profondeur_nulle_deux_points(entete, m.end())
    if pos_deux == -1:
        return [], [], ""
    zone = entete[m.end():pos_deux]
    conclusion = entete[pos_deux + 1:].strip()
    groupes: list = []
    noms: list = []
    pile: list = []
    for i, c in enumerate(zone):
        if c in "([{":
            pile.append((c, i))
        elif c in ")]}":
            if not pile:
                continue
            ouvrant, debut = pile.pop()
            if not pile and ouvrant == "(":
                groupes.append(zone[debut:i + 1])
                noms.extend(_noms_groupe(zone[debut + 1:i]))
    return groupes, noms, conclusion


def _piles_namespaces(texte: str) -> list:
    """Événements `namespace`/`section`/`end` : [(ligne, sens, quoi)].

    `+` = entrée (`namespace` ou `section`), `-` = sortie (`end` nu =
    un niveau, `end X` = jusqu'à X inclus). Seuls les `namespace`
    qualifient les noms ; les `section` sont suivies pour ne pas
    confondre leurs `end` avec des fins de namespace.
    """
    evs = []
    for m in re.finditer(
            r"^(namespace|section)\s+([A-Za-z0-9_.']+)", texte, re.M):
        evs.append((texte[:m.start()].count("\n") + 1, "+",
                    (m.group(1), m.group(2))))
    for m in re.finditer(r"^end\s*([A-Za-z0-9_.']+)?\s*$", texte, re.M):
        evs.append((texte[:m.start()].count("\n") + 1, "-",
                    m.group(1) or ""))
    return sorted(evs)


def _namespaces_de_ligne(evs: list, num: int) -> list:
    """Pile des namespaces englobant la ligne `num` (sections exclues)."""
    pile: list = []
    for ligne, sens, quoi in evs:
        if ligne > num:
            break
        if sens == "+":
            pile.append(quoi)  # (genre, nom)
        elif isinstance(quoi, str) and quoi:
            while pile and pile[-1][1] != quoi:
                pile.pop()
            if pile:
                pile.pop()
        elif pile:
            pile.pop()
    return [nom for genre, nom in pile if genre == "namespace"]


def _opens_fichier(texte: str) -> list:
    """Commandes `open` au niveau du fichier (sans `in`), dans l'ordre.

    Le squelette généré reproduit le contexte d'ouverture des modules
    sources : un identifiant court comme `Integrable` (sous
    `open MeasureTheory`) ne résout que si l'ouverture est rejouée.
    """
    opens, vus = [], set()
    for m in re.finditer(r"^open\s+([^\n]+)$", texte, re.M):
        corps = m.group(1).strip()
        if not corps or re.search(r"(?<![\w'])in(?![\w'])", corps):
            continue  # `open X in <commande>` : portée locale, non rejouable
        if corps not in vus:
            vus.add(corps)
            opens.append("open " + corps)
    return opens


def declarations_dans_fichier(dossier: str, chemin: str
                              ) -> list[DeclarationProuvee]:
    """Toutes les déclarations theorem/lemma d'un fichier, marquées sorry."""
    from phi_complexity.piste_sorry import sorrys_dans_fichier
    lignes = _lignes_decapees(chemin)
    texte = "\n".join(t for _, t in lignes)
    # positions de ligne de chaque déclaration
    marques: list = []
    for m in re.finditer(
            r"^(?:@\[[^\]]*\]\s*)?(theorem|lemma)\s+([A-Za-z0-9_'\.]+)",
            texte, re.M):
        num = texte[:m.start()].count("\n") + 1
        marques.append((num, m.group(1), m.group(2), m.group(0)))
    lignes_sorry = sorted(s.ligne for s in sorrys_dans_fichier(chemin))
    module = module_depuis_fichier(dossier, chemin)
    opens = _opens_fichier(texte)
    # pile des namespaces au fil des déclarations (ordre des lignes)
    evs = _piles_namespaces(texte)
    res: list[DeclarationProuvee] = []
    for i, (num, genre, nom, _) in enumerate(marques):
        pile = _namespaces_de_ligne(evs, num)
        qualifie = nom if "." in nom else ".".join(pile + [nom])
        fin = marques[i + 1][0] if i + 1 < len(marques) else 10 ** 9
        a_sorry = any(num <= ls < fin for ls in lignes_sorry)
        entete = extraire_entete(nom, texte) or ""
        groupes, noms, conclusion = analyser_entete(entete, nom)
        res.append(DeclarationProuvee(
            module=module, fichier=os.path.basename(chemin), nom=nom,
            nom_qualifie=qualifie,
            genre=genre, ligne=num, entete=entete, lieurs=groupes,
            noms_lieurs=noms, conclusion=conclusion, a_sorry=a_sorry,
            opens=list(opens)))
    return res


def _jetons_types(texte: str) -> set:
    return set(re.findall(r"\b[A-Z][A-Za-z0-9_']{2,}\b", texte))


#: Infixes propositionnels reconnus comme tête après dépouillement des
#: lieurs : `a = b` est une proposition `Eq`, pas une proposition « sol ».
#: Le `=` exclut `==`, `=>`, `<=`, `>=`, `!=` par les gardes lookaround.
_INFIXES_TETE = (
    ("↔", "Iff", r"↔"),
    ("=", "Eq", r"(?<![<>=!])=(?![=>])"),
    ("∧", "And", r"∧"),
    ("∨", "Or", r"∨"),
)


def _depouiller_lieurs(texte: str) -> str:
    """Retire les lieurs `∀` de tête : `∀ x, ∀ t ∈ S, P x t` → `P x t`.

    Le dépouillement s'arrête à la première virgule à profondeur 0
    (les virgules dans `(0:ℝ)` ou `(f x, g y)` ne terminent pas un lieur).
    """
    t = (texte or "").strip()
    while t.startswith("∀"):
        prof = 0
        i = 1
        while i < len(t):
            c = t[i]
            if c in "([{":
                prof += 1
            elif c in ")]}":
                prof -= 1
            elif c == "," and prof == 0:
                break
            i += 1
        if i >= len(t):
            break  # `∀` sans corps : texte dégénéré, on garde tel quel
        t = t[i + 1:].strip()
    return t


def _symbole_tete(conclusion: str) -> str:
    """Tête logique d'une proposition : voit à travers les `∀` de tête.

    `∀ x, ∀ t ∈ S, sol₁.u x t = sol₂.u x t` → `Eq` (pas `""`, pas `sol`).
    `∃ T, P T` → `Exists`. Utilisé par le scoreur (pertinence typée) et
    les portes de connexion.
    """
    t = _depouiller_lieurs(conclusion)
    if t.startswith("∃"):
        return "Exists"
    for _, nom, motif in _INFIXES_TETE:
        if re.search(motif, t):
            return nom
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_']*)", t)
    return m.group(1) if m else ""


def _cloture_imports(module: str, graphe: dict) -> set:
    vus = set()
    pile = list(graphe.get(module, []))
    while pile:
        m = pile.pop()
        if m in vus:
            continue
        vus.add(m)
        pile.extend(graphe.get(m, []))
    return vus


def _modules_des_chantiers(chantiers: list, texte_registre: str) -> set:
    """Fichiers .lean mentionnés dans les sections des chantiers donnés."""
    if not texte_registre or not chantiers:
        return set()
    sections = re.split(r"(?m)^###?\s+", texte_registre)
    mods = set()
    noms = {(c["nom"] if isinstance(c, dict) else c).lower()
            for c in chantiers}
    for sec in sections:
        titre = sec[:120].lower()
        if any(n in titre for n in noms):
            for f in re.findall(r"([A-Za-z0-9_]+\.lean)", sec):
                mods.add(f[:-5])  # tige = nom de module (chaînes plates)
    return mods


def _contexte_cablage(sorry: str, dossier: str,
                      chemin_registre: str | None) -> dict:
    """Préambule partagé : piste du sorry + déclarations + notas.

    Retourne {"statut": "INTROUVABLE", ...} ou le contexte avec
    "scored" = [(DeclarationProuvee, score, raisons)] (score > 0).
    """
    from phi_complexity.piste_sorry import (
        graphe_imports, piste,
    )
    p = piste(sorry, dossier, chemin_registre)
    if p["statut"] == "INTROUVABLE":
        return {"statut": "INTROUVABLE", "sorry": sorry}
    occ = p["occurrences"][0]
    module_sorry = module_depuis_fichier(
        dossier, os.path.join(dossier, occ["fichier"]))
    # Les déclarations prouvées de tout le dossier (une passe par fichier)
    decls: list[DeclarationProuvee] = []
    for racine, dirs, fichiers in os.walk(dossier):
        # hors .lake et répertoires cachés : la chaîne, pas ses artefacts
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in sorted(fichiers):
            if f.endswith(".lean"):
                decls.extend(
                    declarations_dans_fichier(
                        dossier, os.path.join(racine, f)))
    ent_sorry = next(
        (d for d in decls
         if d.nom == occ["declaration"] and d.module == module_sorry), None)
    noms_sorry = set(ent_sorry.noms_lieurs) if ent_sorry else set()
    concl_sorry = ent_sorry.conclusion if ent_sorry else ""
    tete_sorry = _symbole_tete(concl_sorry)
    types_sorry = _jetons_types(concl_sorry)

    graphe = graphe_imports(dossier)
    cloture = _cloture_imports(module_sorry, graphe)
    texte_registre = ""
    if chemin_registre and os.path.isfile(chemin_registre):
        with open(chemin_registre, encoding="utf-8") as fh:
            texte_registre = fh.read()
    mods_chantiers = _modules_des_chantiers(
        p["registre"].get("chantiers") or [], texte_registre)

    # Pool des candidats (tout ce qui peut être noté) : la rareté des
    # jetons est relative à ce pool (IDF), pas à une liste en dur.
    pool = [d for d in decls
            if not d.a_sorry
            and not (d.nom == occ["declaration"]
                     and d.module == module_sorry)]
    df: dict = {}
    for d in pool:
        for tok in _jetons_types(d.conclusion):
            df[tok] = df.get(tok, 0) + 1
    idf = _poids_idf(df, len(pool))
    # Rareté des TÊTES logiques (S4) : une tête partagée par presque tout
    # le pool (ex. `Eq` à 85 %) est un bruit quasi pur, pas un signal.
    # Relative au pool, comme l'IDF des jetons — sans constante absolue.
    df_tetes: dict = {}
    for d in pool:
        h = _symbole_tete(d.conclusion)
        if h:
            df_tetes[h] = df_tetes.get(h, 0) + 1
    idf_tetes = _poids_idf(df_tetes, len(pool))
    # Têtes des types des lieurs du sorry : le lemme pertinent consomme
    # ce que le sorry fournit (au niveau des types, pas des noms).
    tetes_lieurs_sorry = set()
    if ent_sorry:
        for _, typ in _lieurs_types(ent_sorry):
            h = _symbole_tete(typ)
            if h:
                tetes_lieurs_sorry.add(h)

    scored: list = []
    for d in pool:
        score, raisons = _noter_declaration(
            d, module_sorry=module_sorry, cloture=cloture,
            mods_chantiers=mods_chantiers, tete_sorry=tete_sorry,
            types_sorry=types_sorry, idf=idf, idf_tetes=idf_tetes,
            tetes_lieurs_sorry=tetes_lieurs_sorry)
        if score <= 0:
            continue
        scored.append((d, score, raisons))
    return {
        "statut": "TROUVÉ",
        "piste": p,
        "occ": occ,
        "module_sorry": module_sorry,
        "ent_sorry": ent_sorry,
        "enonce_sorry": ent_sorry.entete if ent_sorry else "",
        "opens_sorry": ent_sorry.opens if ent_sorry else [],
        "noms_sorry": noms_sorry,
        "concl_sorry": concl_sorry,
        "tete_sorry": tete_sorry,
        "types_sorry": types_sorry,
        "scored": scored,
    }


def _poids_idf(df: dict, n_pool: int) -> dict:
    """Poids de rareté par jeton : log(N/df)/log(N) ∈ [0, 1].

    Un jeton présent dans tout le pool (df = N) pèse 0 : le recouvrement
    lexical sur des jetons génériques (`Set`, `Fin`, …) ne rapporte plus
    rien. Un jeton unique au pool pèse 1. Relatif au pool, sans constante
    absolue. N ≤ 1 → poids nuls (garde log).
    """
    if n_pool <= 1:
        return {tok: 0.0 for tok in df}
    ln = math.log(n_pool)
    return {tok: math.log(n_pool / max(1, c)) / ln for tok, c in df.items()}


def _noter_declaration(d: DeclarationProuvee, *, module_sorry: str,
                       cloture: set, mods_chantiers: set,
                       tete_sorry: str, types_sorry: set,
                       idf: dict | None = None,
                       idf_tetes: dict | None = None,
                       tetes_lieurs_sorry: set | None = None) -> tuple:
    """Note GUIDÉE et expliquée d'une déclaration.

    Composantes : proximité de module comme **a priori faible** (1 / 0.5 /
    1 : circonstancielle, jamais décisive seule), tête logique de conclusion
    **pondérée par rareté** (2 × IDF(tête) : une tête partagée par 85 % du
    pool est un bruit quasi pur), recouvrement de jetons **pondéré par
    rareté** (≤ 3), lieurs dont le type correspond à un lieur du sorry
    (≤ 2 : le lemme consomme ce que le sorry fournit, au niveau des types,
    pas des noms). La preuve typée directe domine l'a priori de module.
    """
    score = 0.0
    raisons: list = []
    if d.module == module_sorry:
        score += 1
        raisons.append("même fichier que le sorry (a priori faible)")
    if d.module in cloture:
        score += 0.5
        raisons.append(
            "dans la clôture d'imports du module du sorry (a priori faible)")
    if d.module in mods_chantiers or d.fichier[:-5] in mods_chantiers:
        score += 1
        raisons.append("module cité par un chantier du registre pour ce sorry")
    if tete_sorry and _symbole_tete(d.conclusion) == tete_sorry:
        poids = idf_tetes.get(tete_sorry, 0.0) if idf_tetes else 1.0
        pts_tete = 2.0 * poids
        if pts_tete > 0:
            score += pts_tete
            raisons.append(
                f"même tête logique de conclusion ({tete_sorry}, "
                f"IDF={poids:.2f})")
    recouv = types_sorry & _jetons_types(d.conclusion)
    if recouv:
        if idf:
            pts = min(3.0, sum(idf.get(t, 0.0) for t in recouv))
            det = sorted(recouv, key=lambda t: -idf.get(t, 0.0))[:5]
        else:
            pts = min(3, len(recouv))
            det = sorted(recouv)[:5]
        if pts > 0:
            score += pts
            raisons.append(
                "types partagés (pondérés par rareté) : "
                + ", ".join(det))
    if tetes_lieurs_sorry:
        vues = set()
        for _, typ in _lieurs_types(d):
            h = _symbole_tete(typ)
            if h and h in tetes_lieurs_sorry:
                vues.add(h)
        if vues:
            pts = min(2, len(vues))
            score += pts
            raisons.append(
                "lieurs typés comme ceux du sorry : "
                + ", ".join(sorted(vues)))
    return score, raisons


def _squelette_mono(d: DeclarationProuvee, noms_sorry: set) -> str:
    """Squelette mono-source historique : lieurs du sorry par nom, `?_` sinon."""
    args = [n if n in noms_sorry else "?_"
            for n in d.noms_lieurs]
    return f"refine {_nom_lean(d)}" + (" " + " ".join(args) if args else "")


# ─────────────────────────────────────────────────────────────
# Synthèse contrôlée de structures (chantier 2026-10-01)
#
# Discipline (docs/INVENTAIRE_SYNTHESE_ENERGY_IDENTITY.md) :
#   1. Dirigée par le type : on ne synthétise que le type exact du trou.
#   2. Ordre d'essai : (a) variable du contexte de type équivalent ;
#      (b) projection d'un champ de structure (1 niveau) ;
#      (c) projection d'une conjonction via dépliage d'une def
#      (avec réordonnancement explicite des lieurs si besoin).
#   3. Jamais d'invention : champs et corps lus dans le corpus, pas en dur.
#   4. Échec propre : None → le trou reste `?_` (Lean tranche).
# Limite honnête : équivalence syntaxique après normalisation + dépliage
# des defs simples — pas de vérification defeq complète (c'est le rôle
# de Lean à la vérification).
# ─────────────────────────────────────────────────────────────

#: Profondeur maximale de dépliage des defs dans la comparaison de types.
_PROF_DEPLIAGE = 4

_cache_structures: dict = {}
_cache_defs: dict = {}


def _structures_corpus(dossier: str) -> dict:
    """nom structure → ([(param, type)], [(champ, type)]) — parsé du corpus."""
    if dossier in _cache_structures:
        return _cache_structures[dossier]
    res: dict = {}
    for racine, _, fichiers in os.walk(dossier):
        if ".lake" in racine:
            continue
        for fn in fichiers:
            if not fn.endswith(".lean"):
                continue
            try:
                with open(os.path.join(racine, fn), encoding="utf-8") as f:
                    lignes = f.readlines()
            except Exception:
                continue
            i = 0
            while i < len(lignes):
                m = re.match(r"^structure\s+(\w+)(.*)\bwhere\b\s*$",
                             lignes[i].rstrip("\n"))
                if m:
                    nom, reste = m.group(1), m.group(2)
                    params = re.findall(r"\(([^():]+):([^()]*)\)", reste)
                    champs = []
                    i += 1
                    while i < len(lignes):
                        lm = re.match(r"^  (\w+)\s*:\s*(.+?)\s*$",
                                      lignes[i].rstrip("\n"))
                        if lm:
                            champs.append((lm.group(1),
                                           _normaliser_type(lm.group(2))))
                        elif lignes[i].startswith(" ") or \
                                lignes[i].strip().startswith("/-"):
                            pass
                        else:
                            break
                        i += 1
                    if champs:
                        res[nom] = (params, champs)
                    continue
                i += 1
    _cache_structures[dossier] = res
    return res


def _defs_corps(dossier: str) -> dict:
    """nom def → corps brut — pour le dépliage en comparaison de types."""
    if dossier in _cache_defs:
        return _cache_defs[dossier]
    res: dict = {}
    for racine, _, fichiers in os.walk(dossier):
        if ".lake" in racine:
            continue
        for fn in fichiers:
            if not fn.endswith(".lean"):
                continue
            try:
                with open(os.path.join(racine, fn), encoding="utf-8") as f:
                    texte = f.read()
            except Exception:
                continue
            # def Nom params... : Type := corps (corps sur une ou plusieurs
            # lignes ; on accumule jusqu'à équilibre des parenthèses).
            # On repère « ^def Nom » puis le « := » qui ouvre le corps
            # (la signature peut contenir des « : » de typage).
            for m in re.finditer(r"^def\s+(\w+)\b", texte, re.M):
                nom = m.group(1)
                if nom in res:
                    continue
                j = texte.find(":=", m.end())
                if j < 0:
                    continue
                # le := doit être avant la prochaine commande top-level
                entre = texte[m.end():j]
                if re.search(r"^(theorem|lemma|def|structure|abbrev|instance|class)\b",
                             entre, re.M):
                    continue
                j += 2
                # Accumulation par lignes : le corps se termine à la
                # prochaine commande top-level (pas au premier ∧ !).
                lignes_corps = []
                while j < len(texte):
                    fin_ligne = texte.find("\n", j)
                    if fin_ligne < 0:
                        fin_ligne = len(texte)
                    ligne = texte[j:fin_ligne]
                    if re.match(r"^(theorem|lemma|def|abbrev|structure|"
                                r"instance|class|end|namespace|open|import|"
                                r"variable|variables|/--|--)\b",
                                ligne.strip()):
                        break
                    lignes_corps.append(ligne)
                    j = fin_ligne + 1
                    if sum(len(l) for l in lignes_corps) > 4000:
                        break
                corps_brut = "\n".join(lignes_corps)
                # commentaire inline en fin de corps (« /-- doc », « -- note »)
                corps_brut = re.split(r"\s/--", corps_brut, maxsplit=1)[0]
                corps_brut = re.split(r"\s--(?![->])", corps_brut, maxsplit=1)[0]
                res[nom] = _normaliser_type(corps_brut)
    _cache_defs[dossier] = res
    return res


class _Deplieur:
    """Dépliage précompilé à sémantique identique à l'ancienne boucle.

    Constat le 2026-10-01 : l'ancienne implémentation balayait tout le
    corpus (583 defs sur energy_identity) avec un regex compilé à la volée
    par nom et par niveau de dépliage — ~6 dépliages par paire (trou,
    terme) rendaient la mesure des directions inutilisable à l'échelle
    réelle (>6 min pour un seul candidat, sans explosion de taille).
    Ici : un seul balayage combiné repère les noms présents, puis la
    substitution reste séquentielle dans l'ordre d'insertion du corpus —
    exactement les mêmes opérations (même ordre, même borne de
    profondeur, même gestion de la récursion directe), sans les centaines
    de recherches vides. Les tests `test_temoin_*` verrouillent la
    sémantique (chaîne en ordre inverse = un cran par passe).
    """

    def __init__(self, defs: dict):
        self.defs = defs
        # miroir exact des conditions de l'ancienne boucle
        self.depliables = {
            nom: corps for nom, corps in defs.items()
            if corps and len(corps) <= 2000 and nom not in corps
        }
        self.ordre = list(self.depliables)
        self.motifs = {
            nom: re.compile(rf"(?<![\w'])({re.escape(nom)})(?![\w'])")
            for nom in self.ordre
        }
        if self.ordre:
            alt = "|".join(re.escape(n) for n in
                           sorted(self.ordre, key=len, reverse=True))
            self.combine = re.compile(rf"(?<![\w'])({alt})(?![\w'])")
        else:
            self.combine = None

    def presents(self, t: str) -> set:
        """Noms dépliables présents dans t (un seul balayage)."""
        if not self.combine:
            return set()
        return {m.group(1) for m in self.combine.finditer(t)}

    def deplier(self, t: str, prof: int = 0) -> str:
        """Remplace les noms de defs simples par leur corps (borné)."""
        if prof >= _PROF_DEPLIAGE or not t:
            return _normaliser_type(t)
        out = _normaliser_type(t)
        presents = self.presents(out)
        for nom in self.ordre:
            if nom not in presents:
                continue
            nouveau = self.motifs[nom].sub(self.depliables[nom], out)
            if nouveau != out:
                out = nouveau
                # le corps substitué peut introduire d'autres noms
                presents |= self.presents(out)
        if out != _normaliser_type(t):
            return self.deplier(out, prof + 1)
        return out

    def reste(self, t: str) -> bool:
        """Reste-t-il un nom dépliable dans t ? (un seul balayage)."""
        return self.combine.search(t) is not None if self.combine else False


_DEPLIEURS: dict = {}


def _deplieur_pour(defs: dict) -> _Deplieur:
    """_Deplieur précompilé pour ce corpus (cache borné, identité vérifiée —
    un id réutilisé après gc ne peut pas empoisonner le cache)."""
    key = id(defs)
    d = _DEPLIEURS.get(key)
    if d is None or d.defs is not defs:
        if len(_DEPLIEURS) > 8:
            _DEPLIEURS.clear()
        d = _Deplieur(defs)
        _DEPLIEURS[key] = d
    return d


def _deplier(t: str, defs: dict, prof: int = 0) -> str:
    """Remplace les noms de defs simples par leur corps (borné).

    Délègue au _Deplieur précompilé : même sémantique, sans les balayages
    vides du corpus (performance à l'échelle réelle)."""
    return _deplieur_pour(defs).deplier(t, prof)


def _types_equivalents(t1: str, t2: str, defs: dict) -> bool:
    """Égalité syntaxique après normalisation + dépliage des defs."""
    if not t1 or not t2:
        return False
    return _deplier(t1, defs) == _deplier(t2, defs)


def _substituer(t: str, subst: dict) -> str:
    """Remplace les noms de lieurs par leurs termes (mots entiers).

    Sans parenthésage ajouté : les termes substitués sont atomiques
    (`sol.u`, `ν`) et le parenthésage casserait l'égalité syntaxique
    en comparaison de types.
    """
    out = t
    for nom, terme in subst.items():
        # pas après un point (s.u ne doit pas devenir s.s.u)
        out = re.sub(rf"(?<![\w'.])({re.escape(nom)})(?![\w'])",
                     terme, out)
    return _normaliser_type(out)


def _chemin_projection_conjonction(idx: int, total: int) -> str:
    """Chemin de projection pour la idx-ème partie (1-indexée) d'une
    conjonction à `total` parties. Lean imbrique les `And` à droite :
    `A ∧ B ∧ C ∧ D` = `And A (And B (And C D))`, donc les projections sont
    `.1`, `.2.1`, `.2.2.1`, `.2.2.2` — jamais `.2`, `.3`, `.4` plats
    (défaut nommé par Lean le 2026-10-01 sur energy_identity)."""
    if idx == 1:
        return ".1"
    if idx == total:
        return ".2" * (total - 1)
    return ".2" * (idx - 1) + ".1"


def _couper_conjonction(corps: str) -> list:
    """Découpe un corps en conjonctions de top-niveau (« A ∧ B ∧ C »)."""
    parts, prof, cur = [], 0, []
    i = 0
    while i < len(corps):
        c = corps[i]
        if c in "([{":
            prof += 1
            cur.append(c)
        elif c in ")]}":
            prof = max(0, prof - 1)
            cur.append(c)
        elif prof == 0 and corps[i:i + 1] == "∧":
            parts.append(_normaliser_type("".join(cur)))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append(_normaliser_type("".join(cur)))
    return [p for p in parts if p]


def _couper_virgule_top(texte: str) -> tuple:
    """Coupe « bindeurs , corps » à la première virgule de top-niveau."""
    prof = 0
    for i, c in enumerate(texte):
        if c in "([{":
            prof += 1
        elif c in ")]}":
            prof = max(0, prof - 1)
        elif c == "," and prof == 0:
            return texte[:i], texte[i + 1:]
    return texte, ""


def _denuder(t: str) -> str:
    """Retire les parenthèses externes redondantes : « (∀ x, P) » → « ∀ x, P »."""
    t = _normaliser_type(t)
    while len(t) >= 2 and t[0] == "(" and t[-1] == ")":
        prof = 0
        ok = True
        for i, c in enumerate(t):
            if c == "(":
                prof += 1
            elif c == ")":
                prof -= 1
            if prof == 0 and i < len(t) - 1:
                ok = False
                break
        if not ok:
            break
        t = _normaliser_type(t[1:-1])
    return t


def _analyse_forall(t: str) -> tuple:
    """Découpe « ∀ b1, ∀ b2, CORPS » → ([(nom, type|None)], corps).

    Gère « ∀ x », « ∀ x i », « ∀ i : Fin 3 », « ∀ t ∈ S » (→ t, puis preuve
    `t ∈ S` marquée « tₘ »). Retourne ([], t) si pas de ∀ de tête.
    """
    lieurs = []
    reste = _denuder(t)
    while True:
        m = re.match(r"^(∀|forall)\s+(.+)$", reste)
        if not m:
            break
        bindeurs, corps = _couper_virgule_top(m.group(2))
        if not corps:
            break
        bindeurs = _normaliser_type(bindeurs)
        # « t ∈ S » ?
        mi = None
        prof = 0
        for i, c in enumerate(bindeurs):
            if c in "([{":
                prof += 1
            elif c in ")]}":
                prof = max(0, prof - 1)
            elif c == "∈" and prof == 0:
                mi = i
                break
        if mi is not None:
            nom = _normaliser_type(bindeurs[:mi])
            ens = _normaliser_type(bindeurs[mi + 1:])
            lieurs.append((nom, None))
            lieurs.append((nom + "ₘ", f"{nom} ∈ {ens}"))
        else:
            mc = re.match(r"^(.+?)\s*:\s*(.+)$", bindeurs)
            if mc:
                noms = mc.group(1).split()
                typ = _normaliser_type(mc.group(2))
                for n in noms:
                    lieurs.append((_normaliser_type(n), typ))
            else:
                for n in bindeurs.split():
                    lieurs.append((_normaliser_type(n), None))
        reste = _denuder(corps)
    return lieurs, reste


def _reordonner_lambda(src_lieurs: list, tgt_lieurs: list,
                       terme: str) -> str | None:
    """Construit « (fun b1 b2 … => terme a1 a2 …) » quand les lieurs cible
    sont une permutation de ceux de la source (comparés par noms).

    Les preuves d'appartenance « t ∈ S » (marquées « tₘ » par
    `_analyse_forall`) sont liées comme `h_t` dans les deux ordres.
    """
    def nom_lie(n: str) -> str:
        return f"h_{n[:-1]}" if n.endswith("ₘ") else n
    noms_src = sorted(n for n, _ in src_lieurs)
    noms_tgt = sorted(n for n, _ in tgt_lieurs)
    if not noms_tgt or noms_src != noms_tgt:
        return None
    params = " ".join(nom_lie(n) for n, _ in tgt_lieurs)
    args = " ".join(nom_lie(n) for n, _ in src_lieurs)
    return f"(fun {params} => {terme} {args})"


def _termes_etendus(contexte: list, structures: dict, subst: dict) -> list:
    """Contexte + projections de structures à 1 niveau : [(terme, type)].

    Ex. `sol : ClassicalSolution ν` → `sol`, `sol.u`, `sol.p`, …,
    `sol.momentum`, … avec leurs types substitués.
    """
    etendus = list(contexte)
    for vnom, vtype in contexte:
        mv = re.match(r"^(\w+)\s*(.*)$", _normaliser_type(vtype))
        if not mv or mv.group(1) not in structures:
            continue
        _params, champs = structures[mv.group(1)]
        sub = dict(subst)
        for fnom, _ in champs:
            sub[fnom] = f"{vnom}.{fnom}"
        for fnom, ftype in champs:
            etendus.append((f"{vnom}.{fnom}", _substituer(ftype, sub)))
    return etendus


def _synthetiser_trou(type_cible: str, contexte: list, structures: dict,
                      defs: dict, subst: dict) -> tuple:
    """Tente de synthétiser un terme du type requis.

    Retourne (terme, raison) ou (None, raison_échec).
    Ordre : terme du contexte étendu (variable ou projection) →
    projection de conjonction (avec réordonnancement si besoin).
    """
    cible = _substituer(type_cible, subst)
    termes_pris = set(subst.values())
    etendus = _termes_etendus(contexte, structures, subst)
    # (a) terme du contexte étendu (pas déjà utilisé par un autre lieur :
    # deux trous distincts ne reçoivent pas le même terme sauf nom identique)
    for tnom, ttype in etendus:
        if tnom in termes_pris:
            continue
        if _types_equivalents(ttype, cible, defs):
            raison = ("lieur du contexte" if "." not in tnom
                      else f"projection {tnom}")
            return tnom, raison
    # (c) projection d'une conjonction (def dont le corps est un ∧)
    for tnom, ttype in etendus:
        if tnom in termes_pris:
            continue
        mv = re.match(r"^(\w+)\s*(.*)$", _normaliser_type(ttype))
        if not mv or mv.group(1) not in defs:
            continue
        nom_def = mv.group(1)
        corps = _deplier(defs[nom_def], defs)
        parts = _couper_conjonction(corps)
        if len(parts) < 2:
            continue
        # Les paramètres de la def portent les mêmes noms que les lieurs
        # substitués (u, p, T) : la substitution par noms suffit ici.
        for idx, part in enumerate(parts, start=1):
            tproj = _substituer(part, subst)
            proj = f"{tnom}{_chemin_projection_conjonction(idx, len(parts))}"
            if _types_equivalents(tproj, cible, defs):
                return proj, f"conjonction {proj}"
            # permutation des ∀ ?
            ls, cs = _analyse_forall(tproj)
            lt, ct = _analyse_forall(cible)
            if ls and lt and _types_equivalents(cs, ct, defs):
                lam = _reordonner_lambda(ls, lt, proj)
                if lam:
                    return lam, f"conjonction {proj} (lieurs réordonnés)"
    return None, "non synthétisable (trou honnête)"


# ---------------------------------------------------------------------------
# Atteignabilité typée à trois zones (chantier 2026-10-01, aval Tomy).
# Discipline : docs/DISCIPLINE_ATTEIGNABILITE_TYPEE.md
# ---------------------------------------------------------------------------

_PAIRES_COERCITION = frozenset({
    frozenset({"ℕ", "ℤ"}), frozenset({"ℕ", "ℚ"}), frozenset({"ℕ", "ℝ"}),
    frozenset({"ℤ", "ℚ"}), frozenset({"ℤ", "ℝ"}), frozenset({"ℚ", "ℝ"}),
    frozenset({"ℝ", "ℂ"}), frozenset({"ℚ", "ℂ"}),
    frozenset({"Fin", "ℕ"}),
})
"""Paires (non ordonnées) de têtes de types entre lesquelles Lean peut
insérer une coercition automatique : un mismatch sur une telle paire n'est
jamais déclaré IMPOSSIBLE (conservateur — INCONNU n'est jamais un
IMPOSSIBLE déguisé). Toute paire manquante sera attrapée par la validation
de solidité (Lean tranche) → durcissement."""


def _est_depliable(nom: str, defs: dict) -> bool:
    """Un nom de def est-il dépliable ? (miroir des conditions de `_deplier`)."""
    corps = defs.get(nom)
    if not corps or len(corps) > 2000:
        return False
    if nom in corps:  # récursion directe
        return False
    return True


def _reste_depliable(t: str, defs: dict) -> bool:
    """Reste-t-il des noms de defs dépliables dans le type normalisé ?

    Un seul balayage via le _Deplieur précompilé (l'ancienne boucle
    testait chaque nom du corpus séparément)."""
    return _deplieur_pour(defs).reste(t)


def _deplier_temoin(t: str, defs: dict) -> tuple:
    """Dépliage avec témoin d'exhaustivité : (type_normalisé, reste).
    `reste` = True ssi la borne a stoppé le dépliage alors qu'il restait
    des définitions dépliables → INCONNU honnête, jamais IMPOSSIBLE."""
    nt = _deplier(t, defs)
    return nt, _reste_depliable(nt, defs)


def _tete(t: str) -> str:
    """Constructeur de tête d'un type normalisé : `∀`, `→`, `∧` ou le
    premier identifiant (tête d'application)."""
    t = _denuder(t)
    if t.startswith("∀"):
        return "∀"
    prof = 0
    for c in t:
        if c in "([{":
            prof += 1
        elif c in ")]}":
            prof = max(0, prof - 1)
        elif prof == 0 and c in "→∧":
            return c
    m = re.match(r"^([\w']+)", t)
    return m.group(1) if m else t[:20]


def _mismatch_irreconciliable(h1: str, h2: str) -> bool:
    """Deux têtes sont-elles prouvablement incompatibles ?
    Conservateur : la paire (∀, →) et toute paire de coercition connue
    rendent INCONNU, jamais IMPOSSIBLE."""
    if h1 == h2:
        return False
    if {h1, h2} <= {"∀", "→"}:
        return False
    if frozenset({h1, h2}) in _PAIRES_COERCITION:
        return False
    return True


def _atteignabilite(type_trou: str, type_terme: str, defs: dict) -> tuple:
    """Classifie (trou, terme) en ATTEIGNABLE / IMPOSSIBLE / INCONNU.

    - ATTEIGNABLE : équivalence après dépliage (± permutation des lieurs).
    - IMPOSSIBLE : dépliage complet des deux côtés, têtes structurellement
      incompatibles. Prétention falsifiable : Lean doit RÉFUTER le câblage.
    - INCONNU : borne atteinte avec du dépliage restant, ou mismatch non
      décisif. Aveu d'ignorance, jamais un IMPOSSIBLE déguisé.
    """
    if _types_equivalents(type_trou, type_terme, defs):
        return "ATTEIGNABLE", "types équivalents après dépliage"
    # permutation des lieurs (miroir de _synthetiser_trou)
    ls, cs = _analyse_forall(_deplier(type_trou, defs))
    lt, ct = _analyse_forall(_deplier(type_terme, defs))
    if ls and lt and _types_equivalents(cs, ct, defs):
        return "ATTEIGNABLE", "équivalents à permutation des lieurs près"
    ntrou, reste_trou = _deplier_temoin(type_trou, defs)
    nterme, reste_terme = _deplier_temoin(type_terme, defs)
    if reste_trou or reste_terme:
        return "INCONNU", "borne de dépliage atteinte, définitions restantes"
    htrou, hterme = _tete(ntrou), _tete(nterme)
    if _mismatch_irreconciliable(htrou, hterme):
        return ("IMPOSSIBLE",
                f"têtes incompatibles après dépliage complet : "
                f"{htrou} vs {hterme}")
    return "INCONNU", "pas d'équivalence, mismatch non décisif"


def classifier_directions_trou(type_cible: str, contexte: list,
                               structures: dict, defs: dict,
                               subst: dict) -> tuple:
    """Pour un trou, classifie chaque terme candidat en trois zones.

    Rend (directions, comptes) ; directions = [(terme, zone, raison)].
    Mesure seulement — ne change pas le comportement de la synthèse.
    """
    cible = _substituer(type_cible, subst)
    etendus = _termes_etendus(contexte, structures, subst)
    directions, vus = [], set()

    def ajouter(terme, ttype):
        if terme in vus:
            return
        vus.add(terme)
        zone, raison = _atteignabilite(cible, _substituer(ttype, subst), defs)
        directions.append((terme, zone, raison))

    for tnom, ttype in etendus:
        ajouter(tnom, ttype)
    # projections de conjonctions (miroir de _synthetiser_trou, chemins imbriqués)
    for tnom, ttype in etendus:
        mv = re.match(r"^(\w+)\s*(.*)$", _normaliser_type(ttype))
        if not mv or mv.group(1) not in defs:
            continue
        parts = _couper_conjonction(_deplier(defs[mv.group(1)], defs))
        if len(parts) < 2:
            continue
        for idx, part in enumerate(parts, start=1):
            proj = f"{tnom}{_chemin_projection_conjonction(idx, len(parts))}"
            ajouter(proj, _substituer(part, subst))
    comptes = {"ATTEIGNABLE": 0, "IMPOSSIBLE": 0, "INCONNU": 0}
    for _, zone, _ in directions:
        comptes[zone] += 1
    return directions, comptes


def _subst_sequentielle(d: DeclarationProuvee, noms_sorry: set,
                        lieurs_sorry: list, structures: dict,
                        defs: dict):
    """Génère (nom_trou, type_trou, subst) trou par trou, avec avancement du
    subst exactement comme `_squelette_synthese` (les trous déjà remplis
    alimentent la substitution des suivants).

    Nécessaire à la solidité du classifieur : un trou dont le type mentionne
    un trou précédent (ex. `hm : u 0 = 0`) ne peut être classé qu'après
    substitution — sinon un faux IMPOSSIBLE (ex. `s.mom` rejeté pour `hm`
    avant que `u` vaille `s.u`, alors que Lean l'accepterait après).
    Ne pas modifier l'un sans l'autre (référence croisée)."""
    subst: dict = {}
    for nom, typ in _lieurs_types(d):
        if nom in noms_sorry:
            subst[nom] = nom
            continue
        yield nom, typ, dict(subst)
        terme, _ = _synthetiser_trou(typ, lieurs_sorry, structures,
                                     defs, subst)
        if terme is not None:
            subst[nom] = terme


def _directions_par_trou(d: DeclarationProuvee, noms_sorry: set,
                         lieurs_sorry: list, structures: dict,
                         defs: dict):
    """Génère (nom_trou, type_substitué, directions) trou par trou.
    Moteur unique de la mesure — `mesurer_directions` et
    `directions_impossibles` sont deux lectures du même flux."""
    for nom, typ, subst in _subst_sequentielle(d, noms_sorry, lieurs_sorry,
                                              structures, defs):
        cible = _substituer(typ, subst)
        directions, _ = classifier_directions_trou(typ, lieurs_sorry,
                                                   structures, defs, subst)
        yield nom, cible, directions


def _mesure_et_impossibles(d: DeclarationProuvee, ctx: dict,
                           dossier: str) -> tuple:
    """(mesure, impossibles) en une seule passe.
    mesure = {"trous": {nom: comptes}, "directions_ouvertes": n} ;
    impossibles = [{"trou", "type_trou", "terme", "raison"}] pour la
    validation de solidité (chacune doit être RÉFUTÉE par Lean)."""
    noms_sorry = ctx["noms_sorry"]
    lieurs_sorry = [(n, t) for n, t in _lieurs_types(ctx["ent_sorry"])] \
        if ctx.get("ent_sorry") else []
    structures = _structures_corpus(dossier)
    defs = _defs_corps(dossier)
    trous, ouvertes, impossibles = {}, 0, []
    for nom, cible, directions in _directions_par_trou(
            d, noms_sorry, lieurs_sorry, structures, defs):
        comptes = {"ATTEIGNABLE": 0, "IMPOSSIBLE": 0, "INCONNU": 0}
        for terme, zone, raison in directions:
            comptes[zone] += 1
            if zone == "IMPOSSIBLE":
                impossibles.append({"trou": nom, "type_trou": cible,
                                    "terme": terme, "raison": raison})
        trous[nom] = comptes
        ouvertes += comptes["ATTEIGNABLE"] + comptes["INCONNU"]
    return {"trous": trous, "directions_ouvertes": ouvertes}, impossibles


def mesurer_directions(d: DeclarationProuvee, ctx: dict,
                       dossier: str) -> dict:
    """Mesure des directions par trou pour un candidat : l'unité.

    `directions_ouvertes` = ATTEIGNABLE + INCONNU, compté exactement sur
    l'ensemble des termes candidats, avec le subst séquentiel de la synthèse.
    Mesure exacte de l'état du classifieur — 100 % fiable en tant que mesure.
    """
    mesure, _ = _mesure_et_impossibles(d, ctx, dossier)
    return mesure


def directions_impossibles(d: DeclarationProuvee, ctx: dict,
                           dossier: str) -> list:
    """Directions prédites IMPOSSIBLE : [{"trou", "type_trou", "terme",
    "raison"}]. Pour la validation de solidité — chacune doit être RÉFUTÉE
    par Lean (sinon violation → durcissement)."""
    _, impossibles = _mesure_et_impossibles(d, ctx, dossier)
    return impossibles


def _squelette_synthese(d: DeclarationProuvee, ctx: dict, dossier: str) -> str:
    """Squelette avec synthèse contrôlée : nom du sorry, sinon synthèse
    typée, sinon `?_` honnête. Les lieurs déjà remplis alimentent la
    substitution pour les trous suivants (u → sol.u avant hmom).
    Ne pas modifier l'avancement du subst sans `_subst_sequentielle`
    (référence croisée — la mesure des directions en dépend)."""
    noms_sorry = ctx["noms_sorry"]
    lieurs_sorry = [(n, t) for n, t in _lieurs_types(ctx["ent_sorry"])] \
        if ctx.get("ent_sorry") else []
    structures = _structures_corpus(dossier)
    defs = _defs_corps(dossier)
    subst: dict = {}
    args = []
    for nom, typ in _lieurs_types(d):
        if nom in noms_sorry:
            args.append(nom)
            subst[nom] = nom
            continue
        terme, _raison = _synthetiser_trou(typ, lieurs_sorry, structures,
                                          defs, subst)
        if terme is None:
            args.append("?_")
        else:
            args.append(terme)
            subst[nom] = terme
    return f"refine {_nom_lean(d)}" + (" " + " ".join(args) if args else "")


def candidats_cablage(sorry: str, dossier: str, chemin_registre: str | None = None,
                      max_candidats: int = 12) -> dict:
    """Candidats de câblage guidés pour un sorry, classés et expliqués."""
    if chemin_registre is None:
        try:
            from phi_complexity.sondes import REGISTRE_DEFAUT
            chemin_registre = REGISTRE_DEFAUT
        except Exception:
            chemin_registre = None
    ctx = _contexte_cablage(sorry, dossier, chemin_registre)
    if ctx["statut"] == "INTROUVABLE":
        return {"statut": "INTROUVABLE", "sorry": sorry, "candidats": []}
    # Tri sur le score AVANT toute synthèse : `_squelette_synthese` est
    # coûteuse (analyse de types, dépliage de defs) et ne doit tourner que
    # sur les retenus, pas sur tout le pool scoré (37015 admissibles pour
    # energy_identity — la version précédente synthétisait tout).
    scored_tries = sorted(ctx["scored"],
                         key=lambda t: (-t[1], t[0].module, t[0].nom))
    bruts: list[Candidat] = []
    for d, score, raisons in scored_tries[:max_candidats]:
        # squelette : lieurs du sorry par nom, sinon synthèse contrôlée
        # de structures, sinon trou `?_` honnête
        squelette = _squelette_synthese(d, ctx, dossier)
        # mesure des directions (trois zones) — annotation seulement :
        # aucun élagage avant validation de solidité par Lean (discipline
        # docs/DISCIPLINE_ATTEIGNABILITE_TYPEE.md, étape 4).
        mesure, impossibles = _mesure_et_impossibles(d, ctx, dossier)
        bruts.append(Candidat(
            sorry=sorry, module_source=d.module, fichier_source=d.fichier,
            declaration=d.nom, ligne=d.ligne, entete_source=d.entete,
            conclusion_source=d.conclusion, squelette=squelette,
            score=score, raisons=raisons, directions=mesure,
            impossibles=impossibles))
    choix = bruts
    choix = bruts
    return {
        "statut": "TROUVÉ",
        "sorry": sorry,
        "module_sorry": ctx["module_sorry"],
        "enonce_sorry": ctx["enonce_sorry"],
        "opens_sorry": ctx.get("opens_sorry", []),
        "candidats": [
            {"declaration": c.declaration, "module": c.module_source,
             "fichier": c.fichier_source, "ligne": c.ligne,
             "conclusion": c.conclusion_source, "squelette": c.squelette,
             "score": c.score, "raisons": c.raisons,
             "directions": c.directions, "impossibles": c.impossibles}
            for c in choix
        ],
    }


# ─────────────────────────────────────────────────────────────
# Multi-lemmes : fragment D + inégalité de budget
# (formulation validée 2026-10-01 — FORMULATION_CHEMINS_MULTI_LEMMES.md)
# ─────────────────────────────────────────────────────────────

#: Identifiant Lean (noms de lieurs, jetons) : lettres Unicode incluses —
#: les indices comme `h₁`, `sol₂` sont des identifiants à part entière, pas
#: `h` / `sol` suivis de bruit. `[^\W\d]` = lettre Unicode ou `_`
#: (les chiffres décimaux `\d` sont exclus au début ; les chiffres
#: souscrits `₁₂`, catégorie « Number, other », sont conservés).
_IDENTIFIANT = r"[^\W\d][\w']*"

#: Mots-clés Lean ignorés par la mesure de complexité Φ.
_MOTS_CLES_PHI = frozenset(
    "theorem lemma def example import open namespace end variable variables "
    "by exact refine have intro apply rw simp aesop auto tauto omega decide "
    "sorry admit fun funext where with in if then else match let do return "
    "Sort Prop Type Nat Int Real abbrev instance class structure".split())

#: Tactiques de recherche INTERDITES dans le fragment D : le générateur ne
#: les émet jamais (Lean vérifie, il ne cherche pas).
_TACTIQUES_RECHERCHE = (
    "simp", "aesop", "auto", "tauto", "omega", "decide", "native_decide",
    "solve_by_elim", "assumption", "contradiction",
)


def _jetons_identifiants(texte: str) -> list:
    """Jetons identifiants d'un texte, mots-clés Lean exclus."""
    return [t for t in re.findall(_IDENTIFIANT, texte)
            if t not in _MOTS_CLES_PHI]


def complexite_declaration(d: DeclarationProuvee) -> int:
    """Φ(d) : complexité mesurée d'une déclaration (lieurs explicites +
    conclusion). Relative et déterministe : aucune constante absolue."""
    return max(1, len(_jetons_identifiants(
        " ".join(d.lieurs) + " " + d.conclusion)))


def budget_sorry(phi_sorry: int, phi_moyen: float) -> dict:
    """Budget B(S), relatif au sorry et à son pool (R1 : rien de décrété).

    n_max = ⌈Φ(S)/Φ̄⌉ + 1 : combien de lemmes moyens tiennent dans la
    complexité de l'énoncé, plus un. B(S) = Φ(S) + n_max·Φ̄.
    """
    phibar = max(1.0, phi_moyen)
    n_max = max(1, math.ceil(max(1, phi_sorry) / phibar) + 1)
    return {"budget": max(1, phi_sorry) + n_max * phibar,
            "n_max": n_max, "phi_moyen": phibar,
            "phi_sorry": max(1, phi_sorry)}


def cout_chemin(phis: list, phi_moyen: float) -> float:
    """C(P) = Σ Φ(l_i) + C(n,2)·Φ̄ : coût des lemmes + pénalité de
    composition super-linéaire, en unités du pool lui-même."""
    n = len(phis)
    return sum(phis) + (n * (n - 1) / 2) * max(1.0, phi_moyen)


def _type_groupe(groupe: str) -> str:
    """Type d'un groupe de lieurs "(ν : ℝ)" → "ℝ" (profondeur réelle)."""
    interieur = groupe.strip()
    if interieur[:1] in "([{":
        interieur = interieur[1:]
    if interieur[-1:] in ")]}":
        interieur = interieur[:-1]
    prof = 0
    for i, c in enumerate(interieur):
        if c in "([{":
            prof += 1
        elif c in ")]}":
            prof = max(0, prof - 1)
        elif c == ":" and prof == 0:
            if interieur[i + 1:i + 2] not in ("=", ":"):
                return interieur[i + 1:].strip()
    return ""


def _lieurs_types(d: DeclarationProuvee) -> list:
    """[(nom, type)] pour chaque lieur explicite d'une déclaration."""
    res = []
    for groupe in d.lieurs:
        type_g = _type_groupe(groupe)
        for nom in _noms_groupe(groupe[1:-1] if len(groupe) >= 2 else ""):
            res.append((nom, type_g))
    return res


def _normaliser_type(texte: str) -> str:
    return re.sub(r"\s+", " ", texte or "").strip()


def types_connectes(t1: str, t2: str) -> bool:
    """Deux types se connectent-ils ? Égalité normalisée, tête identique,
    ou recouvrement de jetons de types (heuristique GUIDÉE, Lean tranche)."""
    if not t1 or not t2:
        return False
    if _normaliser_type(t1) == _normaliser_type(t2):
        return True
    h1, h2 = _symbole_tete(t1), _symbole_tete(t2)
    if h1 and h1 == h2:
        return True
    return len(_jetons_types(t1) & _jetons_types(t2)) >= 1


def _connecte_premier_pas(d: DeclarationProuvee, lieurs_sorry: list) -> bool:
    """Un lemme peut-il ouvrir un chaînage ? Fait inconditionnel, ou au
    moins une hypothèse branchée sur un lieur du sorry."""
    if not d.noms_lieurs:
        return True
    return any(types_connectes(tb, ts)
               for _, tb in _lieurs_types(d)
               for _, ts in lieurs_sorry)


def _connecte_etape(d: DeclarationProuvee, conclusion_precedente: str) -> bool:
    """Une étape consomme-t-elle la précédente ? (chaînage strict : pas de
    paire de lemmes indépendants déguisée en chemin)."""
    if not d.noms_lieurs:
        return True  # fait inconditionnel : s'ajoute à la frontière
    return any(types_connectes(tb, conclusion_precedente)
               for _, tb in _lieurs_types(d))


def _connecte_but(d: DeclarationProuvee, tete_sorry: str,
                  types_sorry: set, concl_sorry: str) -> bool:
    """La conclusion du lemme couvre-t-elle celle du sorry ?"""
    if tete_sorry and _symbole_tete(d.conclusion) == tete_sorry:
        return True
    if _normaliser_type(d.conclusion) == _normaliser_type(concl_sorry):
        return True
    return len(_jetons_types(d.conclusion) & types_sorry) >= 1


def _connexion_forte(a: str, b: str) -> bool:
    """Connexion sans étape logique : égalité normalisée ou même tête.

    Plus stricte que `types_connectes` (qui autorise le recouvrement de
    jetons) : utilisée pour choisir les arguments d'un squelette, où une
    « connexion » doit être utilisable telle quelle.
    """
    na, nb = _normaliser_type(a), _normaliser_type(b)
    return bool(na) and (na == nb
                         or _symbole_tete(na) == _symbole_tete(nb))


def _deballe_nonempty(t: str) -> str | None:
    """Si t est de la forme `Nonempty T`, retourne T ; sinon None."""
    t = _normaliser_type(t)
    if not t.startswith("Nonempty "):
        return None
    reste = t[len("Nonempty "):].strip()
    if len(reste) >= 2 and reste.startswith("(") and reste.endswith(")"):
        prof = 0
        englobante = True
        for i, c in enumerate(reste):
            if c == "(":
                prof += 1
            elif c == ")":
                prof -= 1
            if prof == 0 and i < len(reste) - 1:
                englobante = False
                break
        if englobante and prof == 0:
            reste = reste[1:-1].strip()
    return reste or None


def _choisir_arg(nom_b: str, type_b: str, candidats: list,
                 vus_choix: set) -> tuple:
    """Choisit l'expression d'argument pour un lieur de lemme.

    Passe 1 : même nom + connexion forte (squelettes lisibles).
    Passe 2 : connexion forte, ou déballage `Nonempty T` → `T` par
        `Classical.choice` — l'étape logique est explicite (un `have`
        intermédiaire), jamais prétendue identique ; hypothèse la plus
        récente d'abord.
    Passe 3 : recouvrement large (dernier recours, comme avant).
    Retourne (expression, (nom, type, terme) | None).
    """
    for n, t in candidats:
        if n == nom_b and _connexion_forte(type_b, t):
            return n, None
    for n, t in reversed(candidats):
        if _connexion_forte(type_b, t):
            return n, None
        inner = _deballe_nonempty(t)
        if inner is not None and types_connectes(type_b, inner):
            nom_c = f"{n}_choix"
            deb = None
            if nom_c not in vus_choix:
                vus_choix.add(nom_c)
                deb = (nom_c, inner, f"Classical.choice {n}")
            return nom_c, deb
    for n, t in reversed(candidats):
        if types_connectes(type_b, t):
            return n, None
    return "?_", None


def _args_etape(d: DeclarationProuvee, frontiere: list,
                vus_choix: set) -> tuple:
    """Arguments d'une étape + déballages `Nonempty` éventuels.

    `frontiere` commence par les lieurs du sorry (leurs noms sont
    réutilisés quand le type colle fort) puis les conclusions précédentes.
    Retourne (args, deballages) où deballages = [(nom, type, terme)].
    """
    args, deballages = [], []
    for nom_b, type_b in _lieurs_types(d):
        expr, deb = _choisir_arg(nom_b, type_b, frontiere, vus_choix)
        args.append(expr)
        if deb is not None:
            deballages.append(deb)
    return args, deballages


def _sans_univers_explicites(texte: str) -> str:
    """Retire les annotations d'univers `.{u}` d'un type recopié.

    Le `have` généré n'a pas les `universe u` du module source : sans
    retrait, `u` serait un identifiant inconnu. Lean infère les niveaux
    à l'élaboration ; le retrait ne change pas le sens du type.
    """
    return re.sub(r"\.\{[^{}]*\}", "", texte or "")


def _type_etape(d: DeclarationProuvee, args: list) -> str:
    """Type explicite d'une étape `have` : conclusion du lemme avec les
    noms de lieurs substitués par les arguments réels (`?_` → `_`,
    métavariable unifiée par l'élaboration depuis le type explicite)."""
    table = {}
    for nom, arg in zip(d.noms_lieurs, args):
        table[nom] = "_" if arg == "?_" else arg
    if not table:
        return _sans_univers_explicites(d.conclusion)
    return _sans_univers_explicites(re.sub(
        _IDENTIFIANT,
        lambda m: table.get(m.group(0), m.group(0)),
        d.conclusion))


def _noms_h(n: int, interdits: set) -> list:
    """Noms d'hypothèses h_chem<i> sans collision avec les lieurs du sorry."""
    noms, i = [], 1
    while len(noms) < n:
        cand = f"h_chem{i}"
        if cand not in interdits:
            noms.append(cand)
        i += 1
    return noms


def _squelette_chaine(lemmes: list, lieurs_sorry: list) -> str:
    """Squelette du fragment D pour P = (l_1, …, l_n), n ≥ 2 :

    have h_chem1 : T1 := l1 <args>
    have h_chem1_choix : T := Classical.choice h_chem1  (si déballage Nonempty)
    have h_chem2 : T2 := l2 h_chem1_choix <args>
    exact ln h_chem{n-1} <args>

    Types explicites à chaque étape ; aucune tactique de recherche.
    Les déballages `Nonempty T` → `T` deviennent des `have` explicites
    (`Classical.choice`, une application de terme) et rejoignent la
    frontière pour les étapes suivantes.
    """
    noms_h = _noms_h(len(lemmes) - 1, {n for n, _ in lieurs_sorry})
    frontiere = list(lieurs_sorry)
    vus_choix = set()
    lignes = []
    for i, d in enumerate(lemmes):
        args, deballages = _args_etape(d, frontiere, vus_choix)
        for nom_d, type_d, terme_d in deballages:
            lignes.append(
                f"have {nom_d} : {_sans_univers_explicites(type_d)}"
                f" := {terme_d}")
            frontiere.append((nom_d, type_d))
        appel = _nom_lean(d) + (" " + " ".join(args) if args else "")
        if i < len(lemmes) - 1:
            t_i = _type_etape(d, args)
            lignes.append(f"have {noms_h[i]} : {t_i} := {appel}")
            frontiere.append((noms_h[i], t_i))
        else:
            lignes.append(f"exact {appel}")
    return "\n  ".join(lignes)


@dataclass
class Chemin:
    lemmes: list = field(default_factory=list)  # [DeclarationProuvee]
    noms: list = field(default_factory=list)    # noms des lemmes
    cout: float = 0.0
    score: float = 0.0
    raisons: list = field(default_factory=list)
    squelette: str = ""
    verdict: str = ""
    diagnostic: str = ""


def chemins(sorry: str, dossier: str, chemin_registre: str | None = None,
            max_candidats: int = 12, max_prefixes: int = 20000) -> dict:
    """Chemins multi-lemmes admissibles vers un sorry, par chaînage avant
    GUIDÉ et borné par l'inégalité de budget.

    `max_prefixes` borne le travail de la recherche elle-même (R4) : la
    file est ordonnée par coût croissant, donc les premiers préfixes
    explorés sont les moins chers — arrêter au-delà ne sacrifie que la
    queue exhaustive, jamais les meilleurs candidats. Le dépassement est
    signalé honnêtement (`recherche_bornee`).

    B6 — la MÉMOIRE est bornée elle aussi : seuls les `max_candidats`
    meilleurs chemins sont retenus (top-K exact : la sortie n'utilise
    jamais que ceux-là après tri par (-score, coût, noms)) ; le compteur
    `admissibles_total` reste exact et `admissibles_tronques` signale la
    troncature ; les squelettes ne sont générés que pour les retenus ;
    la file de recherche est plafonnée à 2×max_prefixes (compaction vers
    la moitié la moins chère, signalée par `file_bornee` — même
    justification honnête que max_prefixes).

    Retourne {"statut": "TROUVÉ", "chemins": [...], "budget": B, ...} ou
    {"statut": "INDÉCIDÉ", "a_priori": True, ...} quand aucun chemin
    admissible n'existe dans le fragment D — SANS appeler Lean.
    """
    if chemin_registre is None:
        try:
            from phi_complexity.sondes import REGISTRE_DEFAUT
            chemin_registre = REGISTRE_DEFAUT
        except Exception:
            chemin_registre = None
    ctx = _contexte_cablage(sorry, dossier, chemin_registre)
    if ctx["statut"] == "INTROUVABLE":
        return {"statut": "INTROUVABLE", "sorry": sorry, "chemins": [],
                "admissibles_total": 0, "prefixes_explores": 0}
    scored = ctx["scored"]
    ent_sorry = ctx["ent_sorry"]
    lieurs_sorry = _lieurs_types(ent_sorry) if ent_sorry else []
    phi_s = (complexite_declaration(ent_sorry)
             if ent_sorry else max(1, len(_jetons_identifiants(
                 ctx["enonce_sorry"]))))
    phis_pool = [complexite_declaration(d) for d, _, _ in scored]
    # Φ̄ = MÉDIANE du pool : robuste aux outliers — un lemme géant
    # isolé ne doit pas relâcher le budget de tout le monde.
    phibar = statistics.median(phis_pool) if phis_pool else 1.0
    bud = budget_sorry(phi_s, phibar)
    budget, n_max = bud["budget"], bud["n_max"]
    tete_sorry = ctx["tete_sorry"]
    types_sorry = ctx["types_sorry"]
    concl_sorry = ctx["concl_sorry"]
    phi_par_nom = {_nom_lean(d): complexite_declaration(d)
                   for d, _, _ in scored}

    admissibles_total = 0  # compteur EXACT des chemins complets (int, pas cher)
    # Top-K borné des chemins complets (B6) : la sortie n'utilise jamais
    # que les `max_candidats` premiers après tri par (-score, cout, noms) —
    # ne retenir que ceux-là est EXACT, pas une approximation. `bisect`
    # sur la clé de tri finale ; `seq` départage les égalités de clé pour
    # ne jamais comparer des Chemin entre eux.
    borne_retenus = max_candidats if max_candidats else 1024
    top: list = []  # [(cle, seq, Chemin)] trié croissant = meilleurs d'abord
    seq_top = itertools.count()

    def _retenir(ch: Chemin) -> None:
        nonlocal admissibles_total
        admissibles_total += 1
        cle = (-ch.score, ch.cout, ch.noms)
        bisect.insort(top, (cle, next(seq_top), ch))
        if len(top) > borne_retenus:
            top.pop()  # évince le moins bon (fin de liste triée)

    prefixes_explores = 0

    def _fabriquer(lemmes: list, scores: list, raisons: list) -> Chemin:
        # SANS squelette : sa génération (regex, substitutions) est différée
        # aux seuls retenus finaux — elle n'est jamais sortie que pour eux.
        phis = [phi_par_nom[_nom_lean(d)] for d in lemmes]
        cout = cout_chemin(phis, phibar)
        raisons_ch = list(raisons)
        raisons_ch.append(
            f"inégalité de budget : {cout:g} ≤ {budget:g} "
            f"(Φ(S)={phi_s}, n_max={n_max}, Φ̄={phibar:.1f})")
        return Chemin(lemmes=lemmes, noms=[d.nom for d in lemmes],
                      cout=cout, score=float(sum(scores)),
                      raisons=raisons_ch)

    # n = 1 : régime mono-source historique, filtré par le budget.
    # Un « chemin » de longueur 1 doit ATTEINDRE le but (conclusion
    # connectée) : c'est ce qui distingue chemins() (des chemins vers S)
    # de candidats_cablage() (une piste d'attention, plus large).
    for d, score, raisons in scored:
        if not _connecte_but(d, tete_sorry, types_sorry, concl_sorry):
            continue
        if cout_chemin([phi_par_nom[_nom_lean(d)]], phibar) <= budget:
            _retenir(_fabriquer([d], [score], list(raisons)))

    # n ≥ 2 : chaînage avant par coût croissant, élagué par le budget.
    # Chaque préfixe exploré satisfait déjà C(préfixe) ≤ B(S) : la recherche
    # elle-même est bornée, pas filtrée après coup. `max_prefixes` borne
    # en plus le TRAVAIL (R4) : la file étant ordonnée par coût croissant,
    # les préfixes au-delà de la borne sont les plus chers — les arrêter
    # ne sacrifie que la queue exhaustive. `cap_file` borne la MÉMOIRE de
    # la file (B6) : au-delà, on ne garde que la moitié la moins chère
    # (même justification honnête), signalé par `file_bornee`.
    cap_file = 2 * max_prefixes
    file_bornee = False
    file = []  # (cout, -score, compteur, préfixe, frontière)
    compteur = itertools.count()
    vus = set()
    cache_etape: dict = {}  # (nom_dernier, nom_candidat) -> connecte ?
    for d, score, raisons in scored:
        if not _connecte_premier_pas(d, lieurs_sorry):
            continue
        c = cout_chemin([phi_par_nom[_nom_lean(d)]], phibar)
        if c > budget or n_max < 2:
            continue
        front = (list(lieurs_sorry)
                 + [(f"h0_{d.nom}", d.conclusion)])
        heapq.heappush(file, (c, -score, next(compteur),
                              [(d, score, list(raisons))], front))
    recherche_bornee = False
    while file:
        if prefixes_explores >= max_prefixes:
            recherche_bornee = True
            break
        c, neg_s, _, prefixe, frontiere = heapq.heappop(file)
        prefixes_explores += 1
        if len(prefixe) >= n_max:
            continue
        noms_vus = {_nom_lean(d) for d, _, _ in prefixe}
        dernier = prefixe[-1][0]
        for d, score, raisons in scored:
            if _nom_lean(d) in noms_vus:
                continue  # pas de cycles
            cle = (_nom_lean(dernier), _nom_lean(d))
            conn = cache_etape.get(cle)
            if conn is None:
                conn = _connecte_etape(d, dernier.conclusion)
                cache_etape[cle] = conn
            if not conn:
                continue
            phis_pre = [phi_par_nom[_nom_lean(x)] for x, _, _ in prefixe]
            phis_pre.append(phi_par_nom[_nom_lean(d)])
            c2 = cout_chemin(phis_pre, phibar)
            if c2 > budget:
                continue  # élagage par l'inégalité
            lemmes2 = [x for x, _, _ in prefixe] + [d]
            scores2 = [x for _, x, _ in prefixe] + [score]
            if _connecte_but(d, tete_sorry, types_sorry, concl_sorry):
                # Chemin complet : enregistré (et prolongeable : une
                # complétion peut être le préfixe d'une autre).
                _retenir(_fabriquer(
                    lemmes2, scores2,
                    [r for _, _, rs in prefixe for r in rs] + list(raisons)
                    + ["chaînage : " + " → ".join(
                        x.nom for x in lemmes2)]))
            sig = tuple(_nom_lean(x) for x in lemmes2)
            if sig in vus:
                continue
            vus.add(sig)
            front2 = (list(frontiere)
                      + [(f"h{len(prefixe)}_{d.nom}", d.conclusion)])
            heapq.heappush(
                file, (c2, -sum(scores2), next(compteur),
                       prefixe + [(d, score, list(raisons))], front2))
            if len(file) > cap_file:
                # B6 : la file elle-même est plafonnée (R4 mémoire). La file
                # étant ordonnée par coût croissant, ne garder que la moitié
                # la moins chère ne sacrifie que les préfixes inexplorés les
                # plus chers — même justification honnête que max_prefixes.
                file = heapq.nsmallest(cap_file // 2, file)
                heapq.heapify(file)
                file_bornee = True

    # `top` est déjà trié par (-score, cout, noms) : les retenus finaux.
    # Les squelettes ne sont générés QUE pour eux (jamais sortis sinon).
    for _, _, ch in top:
        if len(ch.lemmes) == 1:
            ch.squelette = _squelette_mono(ch.lemmes[0], ctx["noms_sorry"])
        else:
            ch.squelette = _squelette_chaine(ch.lemmes, lieurs_sorry)
    choix = [ch for _, _, ch in top]
    total = admissibles_total
    tronques = total > len(choix)
    if not choix:
        return {
            "statut": "INDÉCIDÉ",
            "a_priori": True,
            "sorry": sorry,
            "module_sorry": ctx["module_sorry"],
            "raison": ("aucun chemin admissible (C(P) ≤ B(S)) dans le "
                       "fragment D — aucun build Lean brûlé"),
            "budget": bud,
            "candidats_pool": len(scored),
            "prefixes_explores": prefixes_explores,
            "recherche_bornee": recherche_bornee,
            "borne_prefixes": max_prefixes,
            "file_bornee": file_bornee,
            "chemins": [],
            "admissibles_total": 0,
        }
    return {
        "statut": "TROUVÉ",
        "sorry": sorry,
        "module_sorry": ctx["module_sorry"],
        "opens_sorry": ctx["opens_sorry"],
        "enonce_sorry": ctx["enonce_sorry"],
        "budget": bud,
        "candidats_pool": len(scored),
        "prefixes_explores": prefixes_explores,
        "recherche_bornee": recherche_bornee,
        "borne_prefixes": max_prefixes,
        "file_bornee": file_bornee,
        "admissibles_total": total,
        "admissibles_tronques": tronques,
        "chemins_retenus": len(choix),
        "chemins": [
            {"noms": ch.noms,
             "longueur": len(ch.noms),
             "modules": [d.module for d in ch.lemmes],
             "opens": sorted({o for d in ch.lemmes for o in d.opens}),
             "cout": ch.cout,
             "budget": budget,
             "inegalite": f"{ch.cout:g} ≤ {budget:g}",
             "score": ch.score,
             "raisons": ch.raisons,
             "squelette": ch.squelette,
             "verdict": ch.verdict,
             "diagnostic": ch.diagnostic}
            for ch in choix
        ],
    }


def fichier_verification_chemin(sorry: str, chemin: dict, module_sorry: str,
                                lieurs_sorry: list, conclusion_sorry: str,
                                opens_sorry: list | None = None
                                ) -> str:
    """Fichier Lean formellement identifiable comme preuve (ou non) pour un
    chemin multi-lemmes : imports, `example`, squelette du fragment D.

    Les `open` des modules sources (et du module du sorry) sont rejoués
    après les imports : les identifiants courts recopiés (`Integrable`
    sous `open MeasureTheory`, …) résolvent comme à l'origine. Les
    annotations d'univers `.{u}` recopiées sont retirées (pas de
    `universe u` dans le fichier généré ; Lean infère).
    """
    imports = [module_sorry]
    for m in chemin["modules"]:
        if m not in imports:
            imports.append(m)
    opens: list = []
    for o in (opens_sorry or []) + chemin.get("opens", []):
        if o not in opens:
            opens.append(o)
    entete = ("-- Chemin vérifiable généré par phi-complexity "
              "(chemins multi-lemmes, fragment D).")
    lignes = [
        entete,
        f"-- Sorry visé : {sorry} (module {module_sorry}).",
        f"-- Chemin : {' → '.join(chemin['noms'])} "
        f"({chemin['inegalite']}, score {chemin['score']}).",
        "-- Verdict mécanique : ce fichier compile (PROUVÉ) ou Lean nomme",
        "-- formellement ce qui manque (RÉFUTÉ). Aucun sorry ci-dessous.",
        "",
    ]
    lignes += [f"import {m}" for m in imports]
    if opens:
        lignes += [""]
        lignes += opens
    groupes_nus = [_sans_univers_explicites(g) for g in lieurs_sorry]
    concl_nue = _sans_univers_explicites(conclusion_sorry)
    lignes += [
        "",
        f"example {' '.join(groupes_nus)} :",
        f"    {concl_nue} := by",
        f"  {chemin['squelette']}",
        "",
    ]
    return "\n".join(lignes)


def realiser_chemins(sorry: str, dossier: str,
                     chemin_registre: str | None = None,
                     max_candidats: int = 12, verifier: bool = False,
                     timeout_s: int = 600, garder: bool = False,
                     max_prefixes: int = 20000) -> dict:
    """Orchestration : chemins admissibles + (optionnel) verdicts Lean.

    R4 comme propriété : chaque soumission à Lean est PRÉCÉDÉE de l'archive
    de sa preuve d'admissibilité {chemin, coût, budget, inégalité}.
    """
    res = chemins(sorry, dossier, chemin_registre, max_candidats,
                  max_prefixes)
    res["archive"] = []
    if res["statut"] != "TROUVÉ" or not verifier:
        return res
    groupes, _, conclusion = analyser_entete(res["enonce_sorry"], sorry)
    for i, ch in enumerate(res["chemins"]):
        contenu = fichier_verification_chemin(
            sorry, ch, res["module_sorry"], groupes, conclusion,
            opens_sorry=res.get("opens_sorry"))
        chemin_v = os.path.join(
            dossier, f"Verification_{sorry}_chemin{i}.lean")
        entree = {"chemin": ch["noms"], "cout": ch["cout"],
                  "budget": ch["budget"], "inegalite": ch["inegalite"]}
        res["archive"].append(entree)  # AVANT l'appel Lean
        try:
            with open(chemin_v, "w", encoding="utf-8") as fh:
                fh.write(contenu)
            v = verdict_lean(chemin_v, dossier, timeout_s=timeout_s)
        finally:
            if not garder:
                try:
                    os.remove(chemin_v)
                except OSError:
                    pass
        ch["verdict"] = v["verdict"]
        ch["diagnostic"] = v["diagnostic"]
        entree["verdict"] = v["verdict"]
        if garder:
            entree["fichier_verification"] = chemin_v
    return res


def rendre_chemins(res: dict, limite: int = 12) -> str:
    """Rendu console des chemins multi-lemmes."""
    if res["statut"] == "INTROUVABLE":
        return f"❌ Sorry '{res['sorry']}' : INTROUVABLE."
    if res["statut"] == "INDÉCIDÉ":
        b = res["budget"]
        return (
            f"◼ Sorry '{res['sorry']}' ({res['module_sorry']}) : "
            f"INDÉCIDÉ (a priori).\n"
            f"    {res['raison']}\n"
            f"    budget : B={b['budget']:g} (Φ(S)={b['phi_sorry']}, "
            f"n_max={b['n_max']}, Φ̄={b['phi_moyen']:.1f}), "
            f"pool : {res['candidats_pool']} lemme(s), "
            f"{res['prefixes_explores']} préfixe(s) exploré(s).")
    b = res["budget"]
    borne = (" [recherche bornée : "
             f"{res['prefixes_explores']}/{res['borne_prefixes']} préfixes]"
             if res.get("recherche_bornee") else "")
    lignes = [
        f"✅ Sorry '{res['sorry']}' ({res['module_sorry']}) : "
        f"{res['admissibles_total']} chemin(s) admissible(s) "
        f"({res['prefixes_explores']} préfixe(s) exploré(s), "
        f"B={b['budget']:g}){borne}."]
    for i, ch in enumerate(res["chemins"][:limite], 1):
        lignes.append(
            f"  [{i}] {' → '.join(ch['noms'])} "
            f"(coût {ch['cout']:g} ≤ {ch['budget']:g}, score {ch['score']})")
        for r in ch["raisons"]:
            lignes.append(f"      · {r}")
        lignes.append("      squelette :")
        for sl in ch["squelette"].splitlines():
            lignes.append(f"        {sl}")
        if ch.get("verdict"):
            lignes.append(f"      verdict Lean : {ch['verdict']}")
    if res["admissibles_total"] > len(res["chemins"][:limite]):
        lignes.append(
            f"  … et {res['admissibles_total'] - len(res['chemins'][:limite])} "
            f"autre(s) (tranche opérationnelle : --max-candidats).")
    return "\n".join(lignes)


def fichier_verification(sorry: str, candidat: dict, module_sorry: str,
                         lieurs_sorry: list, conclusion_sorry: str,
                         opens_sorry: list | None = None) -> str:
    """Fichier Lean formellement identifiable comme preuve (ou non)."""
    imports = []
    for m in [module_sorry, candidat["module"]]:
        if m not in imports:
            imports.append(m)
    lignes = [
        "-- Candidat de câblage généré par phi-complexity (chemins vérifiables).",
        f"-- Sorry visé : {sorry} (module {module_sorry}).",
        f"-- Source candidate : {candidat['declaration']} "
        f"({candidat['fichier']}:{candidat['ligne']}, score {candidat['score']}).",
        "-- Verdict mécanique : ce fichier compile (PROUVÉ) ou Lean nomme",
        "-- formellement ce qui manque (RÉFUTÉ). Aucun sorry ci-dessous.",
        "",
    ]
    lignes += [f"import {m}" for m in imports]
    # B3 : rejouer les `open` du fichier du sorry, sinon les identifiants
    # comme `Integrable` (sous `open MeasureTheory`) sont inconnus.
    for o in (opens_sorry or []):
        lignes.append(o)
    lignes += [
        "",
        f"example {' '.join(lieurs_sorry)} :",
        f"    {conclusion_sorry} := by",
        f"  {candidat['squelette']}",
        "",
    ]
    return "\n".join(lignes)


def fichier_validation_solidite(sorry: str, module_sorry: str,
                                groupes: list, type_trou: str, terme: str,
                                opens_sorry: list | None = None,
                                raison: str = "") -> str:
    """Test minimal de solidité : une direction prédite IMPOSSIBLE.

    `example <lieurs du sorry> : <type_trou> := <terme>` doit être RÉFUTÉ
    par Lean (type mismatch). Si PROUVÉ : violation de solidité du
    classifieur → durcissement immédiat
    (docs/DISCIPLINE_ATTEIGNABILITE_TYPEE.md).
    """
    lignes = [
        "-- Validation de solidité — atteignabilité typée à trois zones.",
        f"-- Sorry visé : {sorry} (module {module_sorry}).",
        f"-- Direction prédite IMPOSSIBLE : `{terme}`",
        f"--   pour le trou de type : {type_trou}",
        f"-- Raison du classifieur : {raison}",
        "-- Attendu : RÉFUTÉ (type mismatch). PROUVÉ = violation de solidité.",
        "",
        f"import {module_sorry}",
    ]
    # B3 : rejouer les `open` du fichier du sorry (même raison que le
    # fichier de vérification — sinon des identifiants manquent).
    for o in (opens_sorry or []):
        lignes.append(o)
    lignes += [
        "",
        f"example {' '.join(groupes)} : {type_trou} := {terme}",
        "",
    ]
    return "\n".join(lignes)


def interpreter_solidite(v: dict) -> str:
    """CONFIRMÉ / VIOLATION / INCONCLUSIF pour une direction IMPOSSIBLE.

    - PROUVÉ → VIOLATION : le classifieur avait tort, Lean accepte —
      durcissement immédiat (et on a trouvé un câblage qui marche).
    - RÉFUTÉ avec type mismatch → CONFIRMÉ : la prédiction tient.
    - le reste → INCONCLUSIF : le test lui-même est à inspecter
      (identifiant inconnu, univers, timeout), pas une violation.
    """
    if v["verdict"] == "PROUVÉ":
        return "VIOLATION"
    if v["verdict"] == "RÉFUTÉ" and "mismatch" in v["diagnostic"].lower():
        return "CONFIRMÉ"
    return "INCONCLUSIF"


def verdict_lean(chemin_fichier: str, dossier_lean: str,
                 timeout_s: int = 600) -> dict:
    """Fait trancher Lean : PROUVÉ / RÉFUTÉ / INDÉCIDÉ. Mécanique, sans appel."""
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.elan/bin") + ":" + env.get("PATH", "")
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", chemin_fichier],
            cwd=dossier_lean, capture_output=True, text=True,
            timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired:
        return {"verdict": "INDÉCIDÉ",
                "diagnostic": f"timeout après {timeout_s}s — ni preuve ni réfutation"}
    except OSError as exc:
        return {"verdict": "INDÉCIDÉ",
                "diagnostic": f"exécution impossible : {exc}"}
    sortie = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode == 0:
        return {"verdict": "PROUVÉ",
                "diagnostic": "exit 0, aucun sorry dans le fichier généré"}
    lignes = [l for l in sortie.splitlines() if l.strip()]
    return {"verdict": "RÉFUTÉ",
            "diagnostic": "\n".join(lignes[-40:])}


def rendre_console(res: dict) -> str:
    if res["statut"] == "INTROUVABLE":
        return f"❌ Sorry '{res['sorry']}' : INTROUVABLE."
    lignes = [f"✅ Sorry '{res['sorry']}' ({res['module_sorry']}) : "
              f"{len(res['candidats'])} candidat(s)."]
    for i, c in enumerate(res["candidats"], 1):
        lignes.append(
            f"  [{i}] {c['declaration']} ({c['fichier']}:{c['ligne']}) "
            f"score {c['score']}")
        for r in c["raisons"]:
            lignes.append(f"      · {r}")
        lignes.append(f"      squelette : {c['squelette']}")
        if c.get("verdict"):
            lignes.append(
                f"      verdict Lean : {c['verdict']}")
        d = c.get("directions") or {}
        if d.get("trous"):
            lignes.append(
                f"      directions : {d['directions_ouvertes']} ouvertes "
                f"(ATTEIGNABLE + INCONNU) sur {len(d['trous'])} trous, "
                f"{len(c.get('impossibles', []))} prédites IMPOSSIBLE")
    v = res.get("validation_solidite")
    if v:
        lignes.append(
            f"  Validation de solidité : {v['confirmes']} CONFIRMÉ, "
            f"{len(v['violations'])} VIOLATION, "
            f"{len(v['inconclusifs'])} INCONCLUSIF")
        for f in v["violations"]:
            lignes.append(
                f"    ⚠️ VIOLATION : {f['trou']} ← {f['terme'][:60]} "
                f"({f['verdict']})")
    return "\n".join(lignes)
