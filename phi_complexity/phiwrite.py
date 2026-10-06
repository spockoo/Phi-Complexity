"""
phiwrite.py — Générateur de code Lean 4 depuis une spécification
structurée (2026-10-05).

Idée (Tomy) : fermer la boucle de nos instruments. Nous savons lire
(export Lean → `.phiast`), traiter vite (pipeline C), analyser les
goulots (`phi markov`), survivre aux reboots (`phi sentinelle`). Il
manquait l'écriture : phiwrite prend une spécification structurée
(JSON) et produit du code Lean 4 élaborable. Le cycle complet devient :
analyser → générer → élaborer → exporter → vérifier.

Ce que le module fait :
1. CHARGEMENT — lit une spec JSON : {"theorems": [{"name", "imports",
   "statement" (code Lean), "proof" (tactiques ou terme), "doc"}]}.
2. VALIDATION — vérifie la plausibilité syntaxique AVANT d'écrire :
   parenthèses/crochets/accolades équilibrés, pas de `sorry`/`admit`
   sauf flag explicite, imports non vides, champs obligatoires.
   Mode strict par défaut (doctrine : pas de trou déguisé).
3. GÉNÉRATION — produit un `.lean` bien formé : en-tête, imports
   dédupliqués et triés, un théorème par bloc avec docstring.

Limites honnêtes de v1 :
- Validation SYNTAXIQUE seulement (équilibre des délimiteurs,
  présence des champs). Aucune vérification SÉMANTIQUE : seul Lean
  (élaboration) peut dire si le code type. phiwrite ne prouve rien,
  il met en forme.
- Le vérificateur de parenthèses ignore les commentaires de ligne
  (`--`) et les chaînes `"..."`, pas les commentaires de bloc
  imbriqués ni les caractères Unicode spéciaux — rester simple,
  signaler le doute plutôt que de se taire.
- `sorry`/`admit` détectés par mot entier hors commentaires/chaînes ;
  un `sorry` dans un identifiant plus long (ex. `sorryAx`) n'est pas
  un trou.

Zéro dépendance externe : stdlib uniquement. Python 3.11 compatible.
"""

import json
import re
from datetime import datetime, timezone

#: Motifs de trous interdits en mode strict (mots entiers).
TROUS = ("sorry", "admit")

#: Paires de délimiteurs vérifiées.
DELIMITEURS = {"(": ")", "[": "]", "{": "}"}
OUVRANTS = set(DELIMITEURS)
FERMANTS = set(DELIMITEURS.values())

#: Champs obligatoires d'un théorème dans la spec.
CHAMPS_REQUIS = ("name", "statement", "proof")


def _sans_commentaires_ligne(texte: str) -> str:
    """Retire les commentaires de ligne `-- ...` (hors chaînes)."""
    lignes = []
    for ligne in texte.split("\n"):
        hors_chaine = []
        i = 0
        dans_chaine = False
        while i < len(ligne):
            c = ligne[i]
            if c == '"' and (i == 0 or ligne[i - 1] != "\\"):
                dans_chaine = not dans_chaine
                hors_chaine.append(c)
            elif not dans_chaine and c == "-" and ligne[i:i + 2] == "--":
                break
            else:
                hors_chaine.append(c)
            i += 1
        lignes.append("".join(hors_chaine))
    return "\n".join(lignes)


def _sans_chaines(texte: str) -> str:
    """Remplace le contenu des chaînes `"..."` par des espaces (garde
    les positions pour des diagnostics lisibles)."""
    res = []
    i = 0
    dans_chaine = False
    while i < len(texte):
        c = texte[i]
        if c == '"' and (i == 0 or texte[i - 1] != "\\"):
            dans_chaine = not dans_chaine
            res.append(" ")
        elif dans_chaine:
            res.append(" " if c != "\n" else "\n")
        else:
            res.append(c)
        i += 1
    return "".join(res)


def parentheses_equilibrees(texte: str) -> tuple:
    """Vérifie l'équilibre des (), [], {} hors commentaires/chaînes.

    Retourne (True, "") si équilibré, sinon (False, diagnostic).
    """
    nettoye = _sans_chaines(_sans_commentaires_ligne(texte))
    pile = []
    for pos, c in enumerate(nettoye):
        if c in OUVRANTS:
            pile.append((c, pos))
        elif c in FERMANTS:
            if not pile:
                return False, "fermant %r sans ouvrant (position %d)" % (c, pos)
            ouvrant, _ = pile.pop()
            if DELIMITEURS[ouvrant] != c:
                return False, (
                    "mismatch : %r ouvert mais %r fermé (position %d)"
                    % (ouvrant, c, pos))
    if pile:
        ouvrant, pos = pile[-1]
        return False, "ouvrant %r non fermé (position %d)" % (ouvrant, pos)
    return True, ""


def _contient_trou(texte: str) -> str:
    """Retourne le premier motif de trou trouvé (mot entier, hors
    commentaires/chaînes), ou "" si aucun."""
    nettoye = _sans_chaines(_sans_commentaires_ligne(texte))
    for trou in TROUS:
        if re.search(r"\b%s\b" % re.escape(trou), nettoye):
            return trou
    return ""


def charger_spec(chemin: str) -> dict:
    """Charge une spec JSON depuis un fichier."""
    with open(chemin, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("la spec doit être un objet JSON, pas %s"
                         % type(data).__name__)
    return data


def valider_theoreme(thm: dict, index: int,
                     allow_sorry: bool = False) -> list:
    """Valide un théorème de la spec. Retourne la liste des erreurs
    (vide = valide). Ne lève jamais."""
    erreurs = []
    if not isinstance(thm, dict):
        return ["théorème #%d : pas un objet" % index]
    for champ in CHAMPS_REQUIS:
        val = thm.get(champ)
        if not isinstance(val, str) or not val.strip():
            erreurs.append("théorème #%d : champ %r manquant ou vide"
                           % (index, champ))
    nom = thm.get("name", "")
    if isinstance(nom, str) and nom.strip():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_']*$", nom.strip()):
            erreurs.append("théorème #%d : nom %r invalide (identifiant "
                           "Lean attendu)" % (index, nom))
    for champ in ("statement", "proof"):
        code = thm.get(champ)
        if isinstance(code, str) and code.strip():
            ok, diag = parentheses_equilibrees(code)
            if not ok:
                erreurs.append("théorème #%d (%s) : %s"
                               % (index, champ, diag))
    if not allow_sorry:
        for champ in ("statement", "proof"):
            code = thm.get(champ, "")
            if isinstance(code, str):
                trou = _contient_trou(code)
                if trou:
                    erreurs.append(
                        "théorème #%d (%s) : trou %r interdit en mode "
                        "strict (utiliser --allow-sorry pour autoriser)"
                        % (index, champ, trou))
    imports = thm.get("imports", [])
    if imports is not None:
        if not isinstance(imports, list):
            erreurs.append("théorème #%d : imports doit être une liste"
                           % index)
        elif not imports or not any(
                isinstance(i, str) and i.strip() for i in imports):
            erreurs.append("théorème #%d : aucun import non vide"
                           % index)
    return erreurs


def valider_spec(spec: dict, allow_sorry: bool = False) -> list:
    """Valide une spec complète. Retourne la liste des erreurs."""
    erreurs = []
    theorems = spec.get("theorems")
    if not isinstance(theorems, list) or not theorems:
        return ["spec : champ 'theorems' manquant, vide ou pas une liste"]
    noms_vus = set()
    for i, thm in enumerate(theorems):
        erreurs.extend(valider_theoreme(thm, i, allow_sorry=allow_sorry))
        nom = thm.get("name") if isinstance(thm, dict) else None
        if isinstance(nom, str) and nom.strip():
            if nom.strip() in noms_vus:
                erreurs.append("théorème #%d : nom %r dupliqué"
                               % (i, nom.strip()))
            noms_vus.add(nom.strip())
    return erreurs


def _rendre_theoreme(thm: dict) -> str:
    """Rend un théorème en bloc Lean."""
    nom = thm["name"].strip()
    statement = thm["statement"].strip()
    proof = thm["proof"].strip()
    doc = (thm.get("doc") or "").strip()
    lignes = []
    if doc:
        # Docstring /- ... -/ sur plusieurs lignes si besoin.
        doc_lignes = doc.split("\n")
        if len(doc_lignes) == 1:
            lignes.append("/- %s -/" % doc_lignes[0].strip())
        else:
            lignes.append("/-")
            lignes.extend("  " + l.strip() for l in doc_lignes)
            lignes.append("-/")
    lignes.append("theorem %s %s :=" % (nom, statement))
    # Indente la preuve de 2 espaces.
    lignes.extend("  " + l if l.strip() else l
                  for l in proof.split("\n"))
    return "\n".join(lignes)


def generer(spec: dict) -> str:
    """Génère le code Lean complet depuis une spec validée.

    En-tête + imports dédupliqués/triés + un bloc par théorème.
    """
    theorems = spec["theorems"]
    # Imports : union dédupliquée, triée, non vides.
    imports = sorted({
        imp.strip()
        for thm in theorems
        for imp in (thm.get("imports") or [])
        if isinstance(imp, str) and imp.strip()
    })
    horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lignes = [
        "/-",
        "  Généré par phiwrite (phi-complexity) — %s." % horodatage,
        "  Spec : %d théorème(s). Vérification sémantique : à Lean"
        % len(theorems),
        "  (élaboration). Ce fichier met en forme, il ne prouve rien.",
        "-/",
        "",
    ]
    lignes.extend("import %s" % imp for imp in imports)
    lignes.append("")
    lignes.append("")
    blocs = [_rendre_theoreme(thm) for thm in theorems]
    lignes.append(("\n\n" + "-" * 70 + "\n\n").join(blocs))
    lignes.append("")
    return "\n".join(lignes)


def ecrire(spec: dict, chemin_sortie: str,
           allow_sorry: bool = False) -> dict:
    """Valide puis génère et écrit le fichier .lean.

    Retourne {"chemin", "theoremes", "imports"}. Lève ValueError avec
    les erreurs de validation si la spec est invalide.
    """
    erreurs = valider_spec(spec, allow_sorry=allow_sorry)
    if erreurs:
        raise ValueError("spec invalide :\n- " + "\n- ".join(erreurs))
    code = generer(spec)
    with open(chemin_sortie, "w", encoding="utf-8") as fh:
        fh.write(code)
    imports = sorted({
        imp.strip()
        for thm in spec["theorems"]
        for imp in (thm.get("imports") or [])
        if isinstance(imp, str) and imp.strip()
    })
    return {"chemin": chemin_sortie,
            "theoremes": len(spec["theorems"]),
            "imports": imports}


def exemple_spec() -> dict:
    """Spec d'exemple : 3 théorèmes simples et prouvés (pas de sorry)."""
    return {
        "theorems": [
            {
                "name": "phiwrite_exemple_identite",
                "imports": ["Mathlib.Logic.Basic"],
                "statement": "(a : Nat) : a = a",
                "proof": "rfl",
                "doc": "Identité : tout naturel est égal à lui-même.",
            },
            {
                "name": "phiwrite_exemple_add_comm_applique",
                "imports": ["Mathlib.Algebra.Ring.Basic"],
                "statement": "(a b : Nat) : a + b = b + a",
                "proof": "Nat.add_comm a b",
                "doc": "Commutativité de l'addition, appliquée.",
            },
            {
                "name": "phiwrite_exemple_and_intro",
                "imports": ["Mathlib.Logic.Basic"],
                "statement": "(p q : Prop) : p → q → p ∧ q",
                "proof": "fun hp hq => And.intro hp hq",
                "doc": "Introduction de la conjonction.",
            },
        ]
    }
