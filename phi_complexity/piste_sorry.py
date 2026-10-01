"""Piste d'un sorry : trouver le chemin d'un `sorry` à partir de n'importe
quelle complexité à travers Lean 4.

But : pour un sorry nommé (typiquement l'un des 5 du Master Clay), exhiber
son chemin — où il se trouve (fichier, ligne, déclaration englobante),
quels modules en dépendent (graphe d'imports), et, quand le registre vivant
est disponible (opt-in, jamais d'échec sans lui), quels chantiers le servent
avec leurs statuts typés.

L'inventaire est rigoureux : les `sorry` dans les commentaires (`--`,
`/- -/`, docstrings `/-- -/`) et les littéraux de chaînes sont exclus —
un `sorry` ne se déclare pas, il se trouve.
"""

import os
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Décapage rigoureux du source Lean
# ---------------------------------------------------------------------------

_DECL_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+)?"
    r"(theorem|lemma|def|example|instance|abbrev)\s+([A-Za-z0-9_'\.]+)"
)
_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_\.]+)")
_SORRY_RE = re.compile(r"\bsorry\b")


def decaper_lean(src: str):
    """Découpe le source en (n° ligne, texte) sans commentaires ni chaînes.

    Scanner à états : commentaires de ligne `--`, blocs `/- -/` imbriqués
    (dont docstrings `/-- -/`), littéraux `"..."` avec échappements.
    """
    lignes = []
    i, n = 0, len(src)
    ligne_no, buf = 1, []
    bloc = 0
    while i < n:
        c = src[i]
        if c == "\n":
            lignes.append((ligne_no, "".join(buf)))
            ligne_no += 1
            buf = []
            i += 1
            continue
        if bloc:
            if src.startswith("/-", i):
                bloc += 1
                i += 2
            elif src.startswith("-/", i):
                bloc -= 1
                i += 2
            else:
                i += 1
            continue
        if src.startswith("/-", i):
            bloc = 1
            i += 2
            continue
        if src.startswith("--", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == '"':
            i += 1
            while i < n and src[i] != '"':
                if src[i] == "\\":
                    i += 2
                elif src[i] == "\n":
                    break
                else:
                    i += 1
            i += 1
            continue
        buf.append(c)
        i += 1
    lignes.append((ligne_no, "".join(buf)))
    return lignes


# ---------------------------------------------------------------------------
# Inventaire des sorrys
# ---------------------------------------------------------------------------

@dataclass
class Sorry:
    """Un `sorry` réel : fichier, ligne, déclaration englobante."""
    fichier: str
    ligne: int
    declaration: str | None


def sorrys_dans_fichier(chemin: str) -> list:
    """Sorrys réels d'un fichier .lean (commentaires et chaînes exclus)."""
    with open(chemin, "r", encoding="utf-8") as f:
        src = f.read()
    trouves = []
    dernier_decl = None
    for no, texte in decaper_lean(src):
        m = _DECL_RE.match(texte)
        if m:
            dernier_decl = m.group(2)
        for _ in _SORRY_RE.finditer(texte):
            trouves.append(Sorry(fichier=chemin, ligne=no,
                                 declaration=dernier_decl))
    return trouves


def inventorier_sorrys(dossier: str) -> list:
    """Tous les sorrys réels des .lean sous `dossier` (récursif, trié)."""
    res = []
    for racine, dirs, fichiers in os.walk(dossier):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for nom in sorted(fichiers):
            if nom.endswith(".lean"):
                res.extend(sorrys_dans_fichier(os.path.join(racine, nom)))
    res.sort(key=lambda s: (s.fichier, s.ligne))
    return res


# ---------------------------------------------------------------------------
# Graphe d'imports
# ---------------------------------------------------------------------------

def _nom_module(chemin: str, dossier: str) -> str:
    rel = os.path.relpath(chemin, dossier)
    return rel[:-5].replace(os.sep, ".") if rel.endswith(".lean") else rel


def graphe_imports(dossier: str) -> dict:
    """Graphe des imports : nom de module -> modules importés (noms Lean)."""
    graphe = {}
    for racine, dirs, fichiers in os.walk(dossier):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for nom in fichiers:
            if not nom.endswith(".lean"):
                continue
            chemin = os.path.join(racine, nom)
            mod = _nom_module(chemin, dossier)
            imports = []
            with open(chemin, "r", encoding="utf-8") as f:
                for ligne in f:
                    m = _IMPORT_RE.match(ligne)
                    if m:
                        imports.append(m.group(1))
                    elif ligne.strip() and not ligne.startswith("--"):
                        # les imports sont en tête de fichier : on peut
                        # s'arrêter au premier contenu significatif... sauf
                        # que les commentaires de tête utilisent -- ; on
                        # continue par prudence (coût négligeable).
                        pass
            graphe[mod] = imports
    return graphe


def dependants_de(module: str, graphe: dict) -> list:
    """Modules dépendant transitivement de `module` (fermeture inverse)."""
    inverse: dict = {}
    for mod, imports in graphe.items():
        for imp in imports:
            inverse.setdefault(imp, []).append(mod)
    vus, pile = set(), [module]
    while pile:
        courant = pile.pop()
        for dep in inverse.get(courant, []):
            if dep not in vus and dep != module:
                vus.add(dep)
                pile.append(dep)
    return sorted(vus)


# ---------------------------------------------------------------------------
# Registre vivant (opt-in) : trou d'un sorry, chantiers qui le servent
# ---------------------------------------------------------------------------

_RE_TROU_NOMME = re.compile(r"\|\s*(\d+)\s*\(\s*`([^`]+)`\s*\)")


def trou_du_sorry(nom_sorry: str, texte_registre: str):
    """N° de trou du registre pour un sorry nommé (None si absent).

    La numérotation est lue dans le registre lui-même, jamais en dur :
    elle ne suit pas l'ordre des déclarations du Master.
    """
    for m in _RE_TROU_NOMME.finditer(texte_registre):
        if m.group(2).strip() == nom_sorry:
            return int(m.group(1))
    return None


def chantiers_du_sorry(nom_sorry: str, chemin_registre: str) -> dict:
    """Chantiers du registre servant `nom_sorry`, avec statuts typés.

    Retourne {"trou": int|None, "chantiers": [...]} où chaque chantier porte
    nom, statut typé, chantiers cités et référence fichier/ligne.
    """
    from phi_complexity.sondes import RegistreSondes
    with open(chemin_registre, "r", encoding="utf-8") as f:
        texte = f.read()
    trou = trou_du_sorry(nom_sorry, texte)
    reg = RegistreSondes().charger(chemin_registre)
    chantiers = []
    if trou is not None:
        for h in reg.hypotheses.values():
            if trou in (h.trous or []):
                chantiers.append({
                    "nom": h.nom,
                    "statut": h.statut,
                    "chantiers_cites": list(getattr(h, "chantiers", [])),
                    "fichier_ligne": getattr(h, "fichier_ligne", ""),
                })
    # dédupliquer (le registre indexe aussi par alias)
    vus = set()
    uniques = []
    for c in chantiers:
        cle = (c["nom"], c["fichier_ligne"])
        if cle not in vus:
            vus.add(cle)
            uniques.append(c)
    return {"trou": trou, "chantiers": uniques}


# ---------------------------------------------------------------------------
# Piste complète
# ---------------------------------------------------------------------------

def piste(nom_sorry: str, dossier: str, chemin_registre=None) -> dict:
    """Le chemin d'un sorry à travers la complexité Lean 4.

    Retourne un dict à statut typé ("TROUVÉ" / "INTROUVABLE") avec :
    - occurrences : les sorrys réels dans la déclaration `nom_sorry`
    - modules_dependants : fermeture transitive des imports
    - registre : trou + chantiers aux statuts typés (opt-in)
    """
    tous = inventorier_sorrys(dossier)
    occurrences = [s for s in tous if s.declaration == nom_sorry]
    if not occurrences:
        return {"statut": "INTROUVABLE", "sorry": nom_sorry,
                "occurrences": [], "modules_dependants": [],
                "registre": {"disponible": False, "trou": None,
                             "chantiers": []}}
    graphe = graphe_imports(dossier)
    modules_fichier = {_nom_module(s.fichier, dossier) for s in occurrences}
    dependants = set()
    for mod in modules_fichier:
        dependants.update(dependants_de(mod, graphe))
    res = {
        "statut": "TROUVÉ",
        "sorry": nom_sorry,
        "occurrences": [
            {"fichier": s.fichier, "ligne": s.ligne,
             "declaration": s.declaration} for s in occurrences
        ],
        "modules_dependants": sorted(dependants),
    }
    if chemin_registre and os.path.isfile(chemin_registre):
        info = chantiers_du_sorry(nom_sorry, chemin_registre)
        info["disponible"] = True
        res["registre"] = info
    else:
        res["registre"] = {"disponible": False, "trou": None, "chantiers": []}
    return res


def rendre_console(p: dict) -> str:
    """Rendu console de la piste (lisible, sans jargon interne)."""
    if p["statut"] == "INTROUVABLE":
        return f"sorry '{p['sorry']}' : INTROUVABLE dans le dossier."
    lignes = [f"sorry '{p['sorry']}' : TROUVÉ",
              f"  occurrences réelles : {len(p['occurrences'])}"]
    for o in p["occurrences"]:
        lignes.append(f"    - {o['fichier']}:{o['ligne']} "
                      f"(dans {o['declaration']})")
    lignes.append(f"  modules dépendants : {len(p['modules_dependants'])}")
    for m in p["modules_dependants"][:15]:
        lignes.append(f"    - {m}")
    if len(p["modules_dependants"]) > 15:
        lignes.append(f"    … et {len(p['modules_dependants']) - 15} autres")
    reg = p["registre"]
    if reg["disponible"]:
        lignes.append(f"  registre : trou n°{reg['trou']}, "
                      f"{len(reg['chantiers'])} chantiers")
        for c in reg["chantiers"]:
            lignes.append(f"    - [{c['statut']}] {c['nom']} "
                          f"{c['fichier_ligne']}")
    else:
        lignes.append("  registre : non disponible (opt-in) — "
                      "piste structurelle seule")
    return "\n".join(lignes)
