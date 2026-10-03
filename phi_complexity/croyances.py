"""
croyances.py — « Les Chemins Croyants » (v0.10.0).

La carte statique devient une carte *croyante* : chaque symbole reçoit un
score de priorisation inspiré de Bayes — prior structurel (métriques phi
existantes) × vraisemblance (évidence mesurée) → postérieur.

FORMULE (exhibée, pas cachée) :
    log_posterieur(s) = log(1 + complexite(s))
                      + W_sorry  · 1[sorry dans le corps de s]
                      + W_aval   · log(1 + dependants_aval(fichier(s)))
                      + W_churn  · log(1 + commits_30j(fichier(s)))
                      + W_tests  · (capteur non branché : 0)

Chaque terme est écrit dans la sortie JSON (`termes`), chaque poids vit
dans UN seul endroit (`POIDS_CROYANCES`, ci-dessous). Tout facteur est
inspectable : un mécanisme caché n'est pas un instrument.

LIMITES, EN TOUTES LETTRES :
- Ce classement est HEURISTIQUE, d'inspiration bayésienne. Ce N'EST PAS
  un modèle probabiliste calibré : le « postérieur » est un SCORE DE
  PRIORISATION (quel trou attaquer en premier), PAS une probabilité
  d'échec, PAS une prédiction de bug.
- Les poids sont des constantes de jugement, pas des paramètres estimés
  sur données. Ils sont nommés et documentés pour être contestés.
- Sans évidence (pas de git, pas de graphe d'imports résolu), les
  capteurs correspondants rendent un facteur neutre (1.0 en échelle
  multiplicative, 0.0 en log) et SONT SIGNALÉS comme inactifs — jamais
  de zéro silencieux qui fausserait le classement.
- Le capteur `sorry` par grep est un REPLI DÉGRADÉ (v100 n'a pas le flag
  `contient_sorry` du Lecteur Lean — il arrive en v0.9.0) : heuristique
  lexicale à états (commentaires `--`, `/- -/` imbriqués ET littéraux de
  chaîne retirés — durci après dogfood : les `sorry` dans les chaînes de
  diagnostic ne comptent plus), marqué `capteur_degrade`.
"""
import ast
import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import eft
from .core import VERSION
from .eft import CroyanceCertifiee
from .oracle import TraceurOracle
from .editeur.indexeur import (
    EXCLUSIONS_DEFAUT,
    Symbole,
    indexer_projet,
)


# ────────────────────────────────────────────────────────
# POIDS — l'unique endroit où vivent les constantes de jugement.
# Contester un poids = modifier UNE ligne ici, pas chasser un
# coefficient enfoui dans une formule.
# ────────────────────────────────────────────────────────

POIDS_CROYANCES: Dict[str, float] = {
    # Un trou de preuve avoué domine la priorisation : c'est l'application
    # tueuse (« quel sorry attaquer en premier »). Échelle log : +3.0
    # équivaut à multiplier le prior par ~20.
    "sorry": 3.0,
    # Centralité structurelle : un trou dans un fichier importé par
    # beaucoup d'autres a un rayon d'explosion plus grand.
    "aval": 1.0,
    # Instabilité mesurée : un fichier qui bouge beaucoup récemment
    # mérite l'attention (capteur git, neutre hors dépôt).
    "churn": 0.5,
    # Capteur non branché : réservé, documenté, neutre. Le brancher =
    # donner ici un poids non nul ET alimenter le terme.
    "tests": 0.0,
}

PROFONDEUR_AVAL_MAX = 3
"""Borne de la remontée transitive des dépendants : lisible, pas exhaustive."""

FENETRE_CHURN_JOURS = 30
"""Fenêtre du capteur git : commits des 30 derniers jours par fichier."""

FORMULE_EXHIBEE = (
    "log_posterieur = log(1 + complexite)"
    " + W_sorry * 1[sorry]"
    " + W_aval * log(1 + dependants_aval)"
    " + W_churn * log(1 + commits_30j)"
    " + W_tests * 0  (capteur non branché)"
)

LIMITE_HONNETETE = (
    "CLASSEMENT HEURISTIQUE d'inspiration bayésienne — PAS un modèle "
    "probabiliste calibré. Le « postérieur » est un score de priorisation "
    "(quel trou attaquer en premier), PAS une probabilité d'échec ni une "
    "prédiction de bug. Poids = constantes de jugement (voir POIDS_CROYANCES), "
    "pas des paramètres estimés. Capteurs indisponibles = facteur neutre, "
    "signalé dans `capteurs_actifs`."
)


# ────────────────────────────────────────────────────────
# STRUCTURES
# ────────────────────────────────────────────────────────

@dataclass
class CroyanceSymbole:
    """Score de priorisation d'un symbole, termes exhibés."""
    nom: str
    fichier: str          # chemin relatif au dossier analysé (lisible)
    ligne: int
    complexite: int
    posterior: float
    termes: Dict[str, float] = field(default_factory=dict)
    sorry_present: bool = False
    dependants_aval: int = 0
    blast_radius: List[str] = field(default_factory=list)
    # --- EFT opt-in : inertes en mode flottant (défaut) ---
    mode: str = "flottant"
    certifie: Optional[CroyanceCertifiee] = None


# ────────────────────────────────────────────────────────
# CAPTEUR SORRY — natif si le Lecteur Lean l'expose, sinon repli grep.
# ────────────────────────────────────────────────────────

def _lecteur_expose_sorry_natif() -> bool:
    """
    Le flag `contient_sorry` existe-t-il sur MetriqueFonction ?

    Sondage à coût nul (pas d'analyse lancée) : en v100 le Lecteur Lean
    ne l'a pas (il arrive en v0.9.0) → repli grep honnête. Si une version
    future l'ajoute, ce module bascule automatiquement sur le natif.
    """
    try:
        from .modeles import MetriqueFonction
        return hasattr(MetriqueFonction, "contient_sorry")
    except Exception:
        return False


_RE_SORRY = re.compile(r"\b(sorry|admit)\b")


def _nettoyer_lean(lignes: List[str]) -> List[str]:
    """
    Retire les commentaires (`--`, `/- -/` imbriqués) ET les littéraux
    de chaîne (`"..."`, avec échappements `\\` et `\\"`).

    Heuristique LEXICALE à états (pas un parseur) : les guillemets
    délimitent toujours les chaînes en Lean (même `s!"..."`). L'état
    « dans une chaîne » est conservé d'une ligne à l'autre : Lean 4
    autorise le « string gap » (`\\` suivi d'un saut de ligne — la chaîne
    continue). Limites honnêtes : un guillemet orphelin avale la suite du
    fichier (faux négatifs possibles) ; un `"` dans un littéral caractère
    est mal interprété — c'est un REPLI, le flag natif du Lecteur (v0.9.0)
    reste la référence.
    """
    propres: List[str] = []
    prof_bloc = 0  # profondeur d'imbrication des /- -/
    dans_chaine = False  # conservé entre les lignes (string gaps Lean)
    for ligne in lignes:
        out: List[str] = []
        i, n = 0, len(ligne)
        while i < n:
            if prof_bloc > 0:
                if ligne.startswith("/-", i):
                    prof_bloc += 1
                    i += 2
                elif ligne.startswith("-/", i):
                    prof_bloc -= 1
                    i += 2
                else:
                    i += 1
                continue
            c = ligne[i]
            if dans_chaine:
                if c == "\\" and i + 1 < n:
                    i += 2  # caractère échappé : ne pas interpréter le suivant
                    continue
                if c == '"':
                    dans_chaine = False
                i += 1
                continue
            # Hors chaîne : les commentaires ont priorité sur les guillemets
            # (un `"` dans `-- ...` n'ouvre pas de chaîne).
            if ligne.startswith("--", i):
                break  # fin de ligne : commentaire
            if ligne.startswith("/-", i):
                prof_bloc += 1
                i += 2
                continue
            if c == '"':
                dans_chaine = True
                i += 1
                continue
            out.append(c)
            i += 1
        propres.append("".join(out))
    return propres


def _spans_symboles(symboles: List[Symbole], nb_lignes: int) -> Dict[Tuple[str, int], Tuple[int, int]]:
    """
    Étendue (début, fin) en lignes 1-indexées de chaque symbole.

    Heuristique de repli : le corps d'une déclaration va de sa ligne de
    départ à la ligne de départ de la déclaration suivante (ou fin de
    fichier). Suffisant pour localiser un `sorry`, pas pour mesurer.
    Clé = (nom, ligne) : les doublons de nom dans un fichier restent
    distinguables.
    """
    tries = sorted(symboles, key=lambda s: s.ligne)
    spans: Dict[Tuple[str, int], Tuple[int, int]] = {}
    for i, s in enumerate(tries):
        debut = s.ligne
        fin = tries[i + 1].ligne - 1 if i + 1 < len(tries) else nb_lignes
        spans[(s.nom, s.ligne)] = (debut, max(debut, fin))
    return spans


def _sorry_grep_repli(chemin: str, symboles: List[Symbole]) -> Dict[Tuple[str, int], bool]:
    """
    Repli DÉGRADÉ : détecte `sorry`/`admit` par grep hors commentaires.

    Uniquement pour les fichiers Lean (`.lean`) : ailleurs, `sorry`
    n'existe pas comme construction — le capteur est non applicable,
    pas « absent ».
    """
    resultat = {(s.nom, s.ligne): False for s in symboles}
    if os.path.splitext(chemin)[1].lower() != ".lean":
        return resultat
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            lignes = f.read().splitlines()
    except OSError:
        return resultat
    propres = _nettoyer_lean(lignes)
    for (nom, ligne), (debut, fin) in _spans_symboles(symboles, len(lignes)).items():
        corps = "\n".join(propres[debut - 1:fin])
        if _RE_SORRY.search(corps):
            resultat[(nom, ligne)] = True
    return resultat


def _sorry_par_symbole(chemin: str, symboles: List[Symbole]) -> Tuple[Dict[Tuple[str, int], bool], str]:
    """
    Retourne ({(nom, ligne): sorry_present}, mode).

    mode = "analyseur" si le Lecteur expose `contient_sorry` (natif,
    précis), "grep_repli" si on utilise le repli honnête, "non_applicable"
    si le langage n'a pas de `sorry` (ex. Python : le capteur ne s'applique
    pas, le terme vaut 0 — ce n'est pas une absence d'évidence).
    """
    if os.path.splitext(chemin)[1].lower() != ".lean":
        return {(s.nom, s.ligne): False for s in symboles}, "non_applicable"
    if _lecteur_expose_sorry_natif():
        try:
            from .langs import obtenir_analyseur
            # Phase 1 suffit : noms, lignes, flag — pas les métriques.
            res = obtenir_analyseur(chemin).analyser(complet=False)
            par_cle = {(f.nom, int(f.ligne)): bool(getattr(f, "contient_sorry", False))
                       for f in res.fonctions}
            return ({(s.nom, s.ligne): par_cle.get((s.nom, s.ligne), False)
                     for s in symboles}, "analyseur")
        except Exception:
            pass  # repli honnête ci-dessous, jamais de plantage
    return _sorry_grep_repli(chemin, symboles), "grep_repli"


# ────────────────────────────────────────────────────────
# GRAPHE D'IMPORTS — fichier → fichiers importés (résolus sur disque).
# ────────────────────────────────────────────────────────

_RE_IMPORT_LEAN = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)")


def _imports_lean(chemin: str) -> List[str]:
    """
    Modules importés par un fichier Lean (lignes `import A.B.C`).

    On balaie tout le fichier : en Lean les imports sont en tête par
    convention, mais seul le motif `^import` compte — aucun faux positif
    possible sur un `import` mentionné dans un commentaire (`-- import`
    ne matche pas grâce à `^\\s*import`).
    """
    modules: List[str] = []
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            for ligne in f.read().splitlines():
                m = _RE_IMPORT_LEAN.match(ligne)
                if m:
                    modules.append(m.group(1))
    except OSError:
        pass
    return modules


def _imports_python(chemin: str) -> List[str]:
    """
    Modules importés par un fichier Python (via `ast`, robuste).

    Imports relatifs (`from . import x`) résolus contre le paquet du
    fichier. Heuristique documentée : seuls les modules résolvables
    sur disque sous le dossier analysé créent des arêtes.
    """
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            arbre = ast.parse(f.read())
    except (OSError, SyntaxError):
        return []
    modules: List[str] = []
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            modules.extend(a.name for a in noeud.names)
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.level == 0 and noeud.module:
                modules.append(noeud.module)
            elif noeud.level and noeud.names:
                # Relatif : on résout le paquet englobant plus tard,
                # ici on marque avec un préfixe que `_resoudre` comprend.
                base = noeud.module or ""
                modules.append("." * noeud.level + base + ":" +
                               ",".join(a.name for a in noeud.names))
    return modules


def _resoudre_module(dossier_abs: str, module: str, chemin_importeur: str,
                     ext: str) -> Optional[str]:
    """
    Résout un nom de module dotted vers un fichier sous le dossier.

    Retourne le chemin canonique (abspath normalisé) ou None si
    introuvable — un import non résolu n'est PAS une arête inventée.
    """
    if module.startswith("."):
        # Import relatif Python : "." * niveau + "mod" + ":" + noms.
        niveau = len(module) - len(module.lstrip("."))
        reste = module.lstrip(".")
        mod, _, noms = reste.partition(":")
        paquet = os.path.dirname(chemin_importeur)
        for _ in range(niveau - 1):
            paquet = os.path.dirname(paquet)
        base_paquet = os.path.relpath(paquet, dossier_abs).replace(os.sep, ".")
        candidats = []
        for nom in (noms.split(",") if noms else [mod] if mod else []):
            if not nom:
                continue
            dotted = (base_paquet + "." + nom).strip(".")
            candidats.append(dotted)
        if mod:
            candidats.append((base_paquet + "." + mod).strip("."))
    else:
        candidats = [module.split(":")[0]]

    for dotted in candidats:
        if not dotted or dotted == ".":
            continue
        rel = os.path.join(*dotted.split("."))
        for essai in (rel + ext,
                      os.path.join(rel, "__init__" + ext)):
            p = os.path.normpath(os.path.join(dossier_abs, essai))
            if os.path.isfile(p):
                return p
    return None


def _graphe_imports(dossier_abs: str,
                    fichiers: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """
    Construit le DAG fichier → fichiers importés (chemins canoniques).

    Extraction supportée : Python (ast) et Lean (lignes `import`).
    Autres langages : pas d'arêtes — signalé honnêtement par
    `imports_non_resolus` vide et l'absence du langage dans les capteurs.
    Retourne (graphe, imports_non_resolus) — ces derniers plafonnés à 20
    dans la sortie publique, comptés en totalité ici.
    """
    canon = {f: os.path.normpath(os.path.abspath(f)) for f in fichiers}
    par_canon = {c: f for f, c in canon.items()}
    graphe: Dict[str, List[str]] = {c: [] for c in canon.values()}
    non_resolus: List[str] = []

    for f in fichiers:
        c = canon[f]
        ext = os.path.splitext(f)[1].lower()
        if ext == ".lean":
            modules = _imports_lean(f)
        elif ext in (".py", ".pyi"):
            modules = _imports_python(f)
        else:
            continue  # pas d'extraction d'imports : pas d'arêtes inventées
        vus = set()
        for mod in modules:
            cible = _resoudre_module(dossier_abs, mod, c, ext)
            if cible is None:
                non_resolus.append(f"{os.path.relpath(f, dossier_abs)}: {mod}")
            elif cible != c and cible in par_canon and cible not in vus:
                graphe[c].append(cible)
                vus.add(cible)
    return graphe, non_resolus


def _dependants_aval(graphe: Dict[str, List[str]], cible: str,
                     profondeur_max: int = PROFONDEUR_AVAL_MAX) -> List[str]:
    """
    Fichiers qui importent `cible`, transitivement, borné à `profondeur_max`.

    C'est le « blast radius » : si `cible` contient un trou, voilà par où
    l'onde se propage. Borné à 3 pour rester lisible — un rayon exhaustif
    sur un gros graphe noierait le signal.
    """
    inverse: Dict[str, List[str]] = {f: [] for f in graphe}
    for src, dsts in graphe.items():
        for d in dsts:
            inverse.setdefault(d, []).append(src)
    vus = {cible}
    frontiere = [cible]
    for _ in range(profondeur_max):
        suivante: List[str] = []
        for f in frontiere:
            for dep in inverse.get(f, []):
                if dep not in vus:
                    vus.add(dep)
                    suivante.append(dep)
        frontiere = suivante
        if not frontiere:
            break
    vus.discard(cible)
    return sorted(vus)


# ────────────────────────────────────────────────────────
# CAPTEUR CHURN — git log, optionnel, neutre si absent.
# ────────────────────────────────────────────────────────

def _churn_git(dossier_abs: str) -> Dict[str, int]:
    """
    Commits des 30 derniers jours par fichier (chemins canoniques).

    Hors dépôt git, git absent, ou timeout : {} — le capteur est alors
    INACTIF et le terme churn vaut 0 pour tous (facteur neutre), signalé
    dans `capteurs_actifs`. Jamais de churn inventé.
    """
    try:
        racine = subprocess.run(
            ["git", "-C", dossier_abs, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15)
        if racine.returncode != 0:
            return {}
        toplevel = racine.stdout.strip()
        log = subprocess.run(
            ["git", "-C", dossier_abs, "log",
             f"--since={FENETRE_CHURN_JOURS} days ago",
             "--name-only", "--pretty=format:@@@", "--no-merges"],
            capture_output=True, text=True, timeout=30)
        if log.returncode != 0:
            return {}
    except Exception:
        return {}
    comptes: Dict[str, int] = {}
    for ligne in log.stdout.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("@@@"):
            continue
        canon = os.path.normpath(os.path.join(toplevel, ligne))
        comptes[canon] = comptes.get(canon, 0) + 1
    return comptes


# ────────────────────────────────────────────────────────
# FORMULE — un seul endroit où le score est calculé.
# ────────────────────────────────────────────────────────

def _posterior(complexite: int, sorry: bool, dependants: int,
               churn: int) -> Tuple[float, Dict[str, float]]:
    """
    log_posterieur = log(1 + complexite)
                   + W_sorry  * 1[sorry]
                   + W_aval   * log(1 + dependants)
                   + W_churn  * log(1 + churn)
                   + W_tests  * 0

    Retourne (posterior, termes) — les termes sont la preuve exhibée
    du score, pas un détail d'implémentation.
    """
    t_prior = math.log1p(max(0, complexite))
    t_sorry = POIDS_CROYANCES["sorry"] if sorry else 0.0
    t_aval = POIDS_CROYANCES["aval"] * math.log1p(max(0, dependants))
    t_churn = POIDS_CROYANCES["churn"] * math.log1p(max(0, churn))
    t_tests = POIDS_CROYANCES["tests"] * 0.0  # capteur non branché : neutre
    termes = {
        "prior": round(t_prior, 4),
        "sorry": round(t_sorry, 4),
        "aval": round(t_aval, 4),
        "churn": round(t_churn, 4),
        "tests": round(t_tests, 4),
    }
    return round(t_prior + t_sorry + t_aval + t_churn + t_tests, 4), termes


def _posterior_eft(complexite: int, sorry: bool, dependants: int,
                   churn: int, nom: str) -> Tuple[CroyanceCertifiee, Dict[str, float]]:
    """Même formule que `_posterior`, somme en double-double certifiée.

    Les capteurs amont (log1p, poids) sont des flottants déjà arrondis
    quand l'EFT les reçoit — la borne ne couvre que la SOMME PONDÉRÉE
    ci-dessous, à entrées fixées (voir eft.py : PORTÉE DE LA BORNE).
    Les termes exhibés sont identiques au mode flottant (arrondis à 4
    décimales pour présentation) ; l'EFT somme les valeurs BRUTES.
    """
    t_prior = math.log1p(max(0, complexite))
    t_sorry = POIDS_CROYANCES["sorry"] if sorry else 0.0
    t_aval = POIDS_CROYANCES["aval"] * math.log1p(max(0, dependants))
    t_churn = POIDS_CROYANCES["churn"] * math.log1p(max(0, churn))
    t_tests = POIDS_CROYANCES["tests"] * 0.0  # capteur non branché : neutre
    termes = {
        "prior": round(t_prior, 4),
        "sorry": round(t_sorry, 4),
        "aval": round(t_aval, 4),
        "churn": round(t_churn, 4),
        "tests": round(t_tests, 4),
    }
    acc = eft.AccumulateurCertifie()
    for t in (t_prior, t_sorry, t_aval, t_churn, t_tests):
        acc.ajouter(t)
    hi, lo = acc.valeur()
    cert = CroyanceCertifiee(
        nom=nom, hi=hi, lo=lo, borne_erreur=acc.borne_erreur(),
        termes=termes, nb_operations=acc.n_ops,
        magnitude_max=acc.magnitude_max, mode="eft",
    )
    return cert, termes


# ────────────────────────────────────────────────────────
# ENTRÉE PRINCIPALE
# ────────────────────────────────────────────────────────

def chemins_croyants(dossier: str, lang: Optional[str] = None,
                     exclusions=None, top: int = 10,
                     complet: bool = True,
                     exact: Optional[bool] = None,
                     traceur: Optional[TraceurOracle] = None) -> dict:
    """
    Carte croyante d'un dossier : symboles classés par postérieur.

    - `top` : taille de la liste « attaquer en premier » (symboles avec
      sorry, triés par postérieur).
    - `complet` : passé à l'indexeur (phase 1 possible, mais la
      complexité réelle nourrit un meilleur prior).
    - `exact` : arithmétique double-double certifiée (EFT) au lieu du
      flottant. None → drapeau d'environnement PHI_EFT (opt-in) ;
      False explicite → flottant même si PHI_EFT=1. Défaut : flottant.
    - `traceur` : TraceurOracle optionnel — une trace par symbole,
      dans les deux modes (la chaîne prior→évidences→postérieur→action
      ne dépend pas de l'EFT ; seul le mode exact ajoute la borne).

    Retourne un dict JSON-sérialisable. Clés stables : dossier,
    version_phi, nb_fichiers, nb_symboles, poids, formule, capteurs_actifs,
    capteur_degrade, symboles (triés par postérieur décroissant),
    attaquer_en_premier, imports_non_resolus, limites.
    En mode exact s'ajoutent : mode_arithmetique, et par symbole
    posterior_hi, posterior_lo, borne_erreur_certifiee, nb_operations_eft.
    """
    mode_exact = exact if exact is not None else eft.eft_active()
    dossier_abs = os.path.normpath(os.path.abspath(dossier))
    index = indexer_projet(dossier, langage=lang, exclusions=exclusions,
                           complet=complet)
    fichiers = sorted(index.keys())
    canon = {f: os.path.normpath(os.path.abspath(f)) for f in fichiers}

    graphe, non_resolus = _graphe_imports(dossier_abs, fichiers)
    churn = _churn_git(dossier_abs)

    capteurs_actifs = ["prior_structurel", "graphe_imports"]
    capteur_degrade = False
    sorry_modes = set()

    croyances: List[CroyanceSymbole] = []
    # Durcissement 2026-10-02 : par fichier, combien de symboles viennent
    # du repli robuste (tree-sitter aveugle). Jamais silencieux.
    extraction_repli: Dict[str, int] = {}
    for f in fichiers:
        c = canon[f]
        rel = os.path.relpath(c, dossier_abs)
        symboles = sorted(index[f], key=lambda s: s.ligne)
        n_repli = sum(1 for s in symboles
                      if getattr(s, "extraction", "analyseur") == "robuste_repli")
        if n_repli:
            extraction_repli[rel] = n_repli
        sorry_map, mode = _sorry_par_symbole(f, symboles)
        sorry_modes.add(mode)
        if mode == "grep_repli":
            capteur_degrade = True
        dependants = _dependants_aval(graphe, c)
        n_churn = churn.get(c, 0)
        for s in symboles:
            sp = sorry_map.get((s.nom, s.ligne), False)
            if mode_exact:
                cert, termes = _posterior_eft(
                    int(s.complexite), sp, len(dependants), n_churn,
                    nom=f"{rel}::{s.nom}")
                posterior = cert.valeur
            else:
                posterior, termes = _posterior(int(s.complexite), sp,
                                               len(dependants), n_churn)
                cert = None
            croyances.append(CroyanceSymbole(
                nom=s.nom, fichier=rel, ligne=int(s.ligne),
                complexite=int(s.complexite), posterior=posterior,
                termes=termes, sorry_present=sp,
                dependants_aval=len(dependants),
                blast_radius=[os.path.relpath(d, dossier_abs)
                              for d in dependants],
                mode="eft" if mode_exact else "flottant",
                certifie=cert,
            ))

    if "grep_repli" in sorry_modes:
        capteurs_actifs.append("sorry:grep_repli")
    if "analyseur" in sorry_modes:
        capteurs_actifs.append("sorry:analyseur_natif")
    # "non_applicable" (ex. Python) : le capteur sorry ne s'applique pas —
    # on ne l'affiche ni comme actif ni comme dégradé : il est hors sujet.
    if churn:
        capteurs_actifs.append(f"churn_git:{FENETRE_CHURN_JOURS}j")

    croyances.sort(key=lambda b: (b.certifie.valeur if b.certifie is not None
                                   else b.posterior), reverse=True)
    trous = [b for b in croyances if b.sorry_present][:max(0, top)]

    def _vers_dict(b: CroyanceSymbole, avec_blast: bool = False) -> dict:
        d = {
            "nom": b.nom,
            "fichier": b.fichier,
            "ligne": b.ligne,
            "complexite": b.complexite,
            "posterior": b.posterior,
            "termes": b.termes,
            "sorry_present": b.sorry_present,
            "dependants_aval": b.dependants_aval,
        }
        if b.certifie is not None:
            # Clés EFT : UNIQUEMENT en mode exact. Le JSON du mode
            # flottant (défaut) reste bit-identique à la v0.11.0.
            d["posterior_hi"] = b.certifie.hi
            d["posterior_lo"] = b.certifie.lo
            d["borne_erreur_certifiee"] = b.certifie.borne_erreur
            d["nb_operations_eft"] = b.certifie.nb_operations
        if avec_blast:
            d["blast_radius"] = b.blast_radius
        return d

    if traceur is not None:
        trous_ids = {id(b) for b in trous}
        for b in croyances:
            c = b.certifie
            traceur.enregistrer(
                moteur="croyances", cible=dossier_abs,
                symbole=f"{b.fichier}::{b.nom}",
                prior=b.termes.get("prior", 0.0),
                termes_evidence=dict(b.termes),
                posterior=b.posterior,
                posterior_hi=(c.hi if c else None),
                posterior_lo=(c.lo if c else None),
                borne_erreur=(c.borne_erreur if c else None),
                decision=("attaquer_en_premier" if id(b) in trous_ids
                          else "non_prioritaire"),
                mode="eft" if mode_exact else "flottant",
            )

    resultat = {
        "dossier": dossier,
        "version_phi": VERSION,
        "nb_fichiers": len(fichiers),
        "nb_symboles": len(croyances),
        "poids": dict(POIDS_CROYANCES),
        "formule": FORMULE_EXHIBEE,
        "capteurs_actifs": capteurs_actifs,
        "capteur_degrade": capteur_degrade,
        "symboles": [_vers_dict(b) for b in croyances],
        "attaquer_en_premier": [_vers_dict(b, avec_blast=True) for b in trous],
        "imports_non_resolus": non_resolus[:20],
        "nb_imports_non_resolus": len(non_resolus),
        "limites": LIMITE_HONNETETE,
        # Durcissement 2026-10-02 : fichiers où le repli robuste a récupéré
        # des symboles invisibles à tree-sitter ({fichier_rel: n}).
        # Absent/vide = extraction nominale partout.
        "extraction_repli": extraction_repli,
    }
    if mode_exact:
        # Clé EFT : uniquement en mode exact (JSON flottant inchangé).
        resultat["mode_arithmetique"] = "eft"
    return resultat


def chemins_console(carte: dict) -> str:
    """Rend la carte croyante lisible dans un terminal."""
    lignes = [
        "╔════════════════════════════════════════════════════════════════════╗",
        "║         PHI-COMPLEXITY — LES CHEMINS CROYANTS  🧭                  ║",
        "╚════════════════════════════════════════════════════════════════════╝",
        "",
        f"  📁 Dossier   : {carte['dossier']}",
        f"  📚 Fichiers  : {carte['nb_fichiers']}    🔣 Symboles : {carte['nb_symboles']}",
        f"  🧪 Capteurs  : {', '.join(carte['capteurs_actifs'])}",
    ]
    if carte["capteur_degrade"]:
        lignes.append("  ⚠ Capteur sorry DÉGRADÉ (repli grep hors commentaires) — "
                      "le flag natif du Lecteur Lean arrive en v0.9.0.")
    lignes += [
        "",
        "  ┌─ ATTAQUER EN PREMIER (trous de preuve, triés par postérieur) ─",
    ]
    trous = carte["attaquer_en_premier"]
    if not trous:
        lignes.append("  │  ✓ Aucun `sorry`/`admit` détecté — rien à attaquer.")
    for i, t in enumerate(trous, 1):
        termes = t["termes"]
        lignes.append(
            f"  │  {i}. {t['nom']} — {t['fichier']}:{t['ligne']} "
            f"(postérieur {t['posterior']})"
        )
        lignes.append(
            f"  │     termes: prior={termes['prior']} sorry={termes['sorry']} "
            f"aval={termes['aval']} churn={termes['churn']} "
            f"│ blast_radius={t['dependants_aval']} fichiers"
        )
        if t["blast_radius"]:
            noms = ", ".join(os.path.basename(b) for b in t["blast_radius"][:5])
            plus = f" (+{len(t['blast_radius']) - 5})" if len(t["blast_radius"]) > 5 else ""
            lignes.append(f"  │     ↳ dépendants : {noms}{plus}")
    lignes += [
        "  └" + "─" * 68,
        "",
        "  TOP-5 SYMBOLES (tous, par postérieur) :",
    ]
    for i, s in enumerate(carte["symboles"][:5], 1):
        flag = " 🕳" if s["sorry_present"] else ""
        lignes.append(
            f"    {i}. {s['nom']}{flag} — {s['fichier']}:{s['ligne']} "
            f"(postérieur {s['posterior']}, complexité {s['complexite']})"
        )
    lignes += [
        "",
        "  LIMITES : " + carte["limites"],
    ]
    return "\n".join(lignes)
