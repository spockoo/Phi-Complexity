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
"""
from __future__ import annotations

import os
import re
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
            return re.findall(r"[A-Za-z_ν][A-Za-z0-9_'ν]*", contenu[:i])
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
    res: list[DeclarationProuvee] = []
    for i, (num, genre, nom, _) in enumerate(marques):
        fin = marques[i + 1][0] if i + 1 < len(marques) else 10 ** 9
        a_sorry = any(num <= ls < fin for ls in lignes_sorry)
        entete = extraire_entete(nom, texte) or ""
        groupes, noms, conclusion = analyser_entete(entete, nom)
        res.append(DeclarationProuvee(
            module=module, fichier=os.path.basename(chemin), nom=nom,
            genre=genre, ligne=num, entete=entete, lieurs=groupes,
            noms_lieurs=noms, conclusion=conclusion, a_sorry=a_sorry))
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


def candidats_cablage(sorry: str, dossier: str, chemin_registre: str | None = None,
                      max_candidats: int = 12) -> dict:
    """Candidats de câblage guidés pour un sorry, classés et expliqués."""
    from phi_complexity.piste_sorry import (
        graphe_imports, piste,
    )
    if chemin_registre is None:
        try:
            from phi_complexity.sondes import REGISTRE_DEFAUT
            chemin_registre = REGISTRE_DEFAUT
        except Exception:
            chemin_registre = None
    p = piste(sorry, dossier, chemin_registre)
    if p["statut"] == "INTROUVABLE":
        return {"statut": "INTROUVABLE", "sorry": sorry, "candidats": []}
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

    bruts: list[Candidat] = []
    for d in decls:
        if d.a_sorry:
            continue  # jamais une source contenant un sorry
        if d.nom == occ["declaration"] and d.module == module_sorry:
            continue  # pas le sorry lui-même
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
        if score <= 0:
            continue
        # squelette : lieurs du sorry par nom, le reste en trous nommés
        args = [n if n in noms_sorry else "?_"
                for n in d.noms_lieurs]
        squelette = f"refine {d.nom}" + (" " + " ".join(args) if args else "")
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
        "module_sorry": module_sorry,
        "enonce_sorry": ent_sorry.entete if ent_sorry else "",
        "candidats": [
            {"declaration": c.declaration, "module": c.module_source,
             "fichier": c.fichier_source, "ligne": c.ligne,
             "conclusion": c.conclusion_source, "squelette": c.squelette,
             "score": c.score, "raisons": c.raisons}
            for c in choix
        ],
    }


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
