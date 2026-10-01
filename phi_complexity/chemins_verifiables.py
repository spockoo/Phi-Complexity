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
- classement GUIDÉ et expliqué (même fichier, clôture d'imports, chantiers
  du registre, recouvrement de conclusion) — pas d'explosion combinatoire
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


def _symbole_tete(conclusion: str) -> str:
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_']*)", conclusion)
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

    scored: list = []
    for d in decls:
        if d.a_sorry:
            continue  # jamais une source contenant un sorry
        if d.nom == occ["declaration"] and d.module == module_sorry:
            continue  # pas le sorry lui-même
        score, raisons = _noter_declaration(
            d, module_sorry=module_sorry, cloture=cloture,
            mods_chantiers=mods_chantiers, tete_sorry=tete_sorry,
            types_sorry=types_sorry)
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


def _noter_declaration(d: DeclarationProuvee, *, module_sorry: str,
                       cloture: set, mods_chantiers: set,
                       tete_sorry: str, types_sorry: set) -> tuple:
    """Note GUIDÉE et expliquée d'une déclaration (extraite de
    candidats_cablage, comportement inchangé)."""
    score = 0.0
    raisons: list = []
    if d.module == module_sorry:
        score += 3
        raisons.append("même fichier que le sorry")
    if d.module in cloture:
        score += 2
        raisons.append("dans la clôture d'imports du module du sorry")
    if d.module in mods_chantiers or d.fichier[:-5] in mods_chantiers:
        score += 2
        raisons.append("module cité par un chantier du registre pour ce sorry")
    if tete_sorry and _symbole_tete(d.conclusion) == tete_sorry:
        score += 2
        raisons.append(
            f"même tête de conclusion ({tete_sorry})")
    recouv = types_sorry & _jetons_types(d.conclusion)
    if recouv:
        pts = min(3, len(recouv))
        score += pts
        raisons.append(
            "types partagés : " + ", ".join(sorted(recouv)[:5]))
    return score, raisons


def _squelette_mono(d: DeclarationProuvee, noms_sorry: set) -> str:
    """Squelette mono-source historique : lieurs du sorry par nom, `?_` sinon."""
    args = [n if n in noms_sorry else "?_"
            for n in d.noms_lieurs]
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
    noms_sorry = ctx["noms_sorry"]
    bruts: list[Candidat] = []
    for d, score, raisons in ctx["scored"]:
        # squelette : lieurs du sorry par nom, le reste en trous nommés
        squelette = _squelette_mono(d, noms_sorry)
        bruts.append(Candidat(
            sorry=sorry, module_source=d.module, fichier_source=d.fichier,
            declaration=d.nom, ligne=d.ligne, entete_source=d.entete,
            conclusion_source=d.conclusion, squelette=squelette,
            score=score, raisons=raisons))
    bruts.sort(key=lambda c: (-c.score, c.module_source, c.declaration))
    choix = bruts[:max_candidats]
    return {
        "statut": "TROUVÉ",
        "sorry": sorry,
        "module_sorry": ctx["module_sorry"],
        "enonce_sorry": ctx["enonce_sorry"],
        "candidats": [
            {"declaration": c.declaration, "module": c.module_source,
             "fichier": c.fichier_source, "ligne": c.ligne,
             "conclusion": c.conclusion_source, "squelette": c.squelette,
             "score": c.score, "raisons": c.raisons}
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

    admissibles: list[Chemin] = []
    prefixes_explores = 0

    def _fabriquer(lemmes: list, scores: list, raisons: list,
                   squelette: str) -> Chemin:
        phis = [phi_par_nom[_nom_lean(d)] for d in lemmes]
        cout = cout_chemin(phis, phibar)
        raisons_ch = list(raisons)
        raisons_ch.append(
            f"inégalité de budget : {cout:g} ≤ {budget:g} "
            f"(Φ(S)={phi_s}, n_max={n_max}, Φ̄={phibar:.1f})")
        return Chemin(lemmes=lemmes, noms=[d.nom for d in lemmes],
                      cout=cout, score=float(sum(scores)),
                      raisons=raisons_ch, squelette=squelette)

    # n = 1 : régime mono-source historique, filtré par le budget.
    # Un « chemin » de longueur 1 doit ATTEINDRE le but (conclusion
    # connectée) : c'est ce qui distingue chemins() (des chemins vers S)
    # de candidats_cablage() (une piste d'attention, plus large).
    for d, score, raisons in scored:
        if not _connecte_but(d, tete_sorry, types_sorry, concl_sorry):
            continue
        if cout_chemin([phi_par_nom[_nom_lean(d)]], phibar) <= budget:
            admissibles.append(_fabriquer(
                [d], [score], list(raisons),
                _squelette_mono(d, ctx["noms_sorry"])))

    # n ≥ 2 : chaînage avant par coût croissant, élagué par le budget.
    # Chaque préfixe exploré satisfait déjà C(préfixe) ≤ B(S) : la recherche
    # elle-même est bornée, pas filtrée après coup. `max_prefixes` borne
    # en plus le TRAVAIL (R4) : la file étant ordonnée par coût croissant,
    # les préfixes au-delà de la borne sont les plus chers — les arrêter
    # ne sacrifie que la queue exhaustive.
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
                ch = _fabriquer(
                    lemmes2, scores2,
                    [r for _, _, rs in prefixe for r in rs] + list(raisons)
                    + ["chaînage : " + " → ".join(
                        x.nom for x in lemmes2)],
                    _squelette_chaine(lemmes2, lieurs_sorry))
                admissibles.append(ch)
            sig = tuple(_nom_lean(x) for x in lemmes2)
            if sig in vus:
                continue
            vus.add(sig)
            front2 = (list(frontiere)
                      + [(f"h{len(prefixe)}_{d.nom}", d.conclusion)])
            heapq.heappush(
                file, (c2, -sum(scores2), next(compteur),
                       prefixe + [(d, score, list(raisons))], front2))

    admissibles.sort(key=lambda ch: (-ch.score, ch.cout, ch.noms))
    total = len(admissibles)
    choix = admissibles[:max_candidats] if max_candidats else admissibles
    if not admissibles:
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
        "admissibles_total": total,
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
                         lieurs_sorry: list, conclusion_sorry: str) -> str:
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
    lignes += [
        "",
        f"example {' '.join(lieurs_sorry)} :",
        f"    {conclusion_sorry} := by",
        f"  {candidat['squelette']}",
        "",
    ]
    return "\n".join(lignes)


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
    return "\n".join(lignes)
