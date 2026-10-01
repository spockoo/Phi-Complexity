"""
durcissement.py — Batterie de durcissement des sondes phi-complexity (v0.11.0).

Doctrine (Tomy, 2026-09-30) : un instrument qui échoue doit être durci,
jamais abandonné ; un instrument doit échouer BRUYAMMENT, jamais en silence.

Ce module ne modifie AUCUN instrument existant : il les soumet à des
sabotages contrôlés (S1), diagnostique leurs capteurs (S2/S5), mène des
tentatives red-team documentées (S3) et applique la règle de provenance
(S4 : méthode + périmètre + date + version sur chaque mesure).

SÉCURITÉ : les sabotages n'opèrent QUE sur des copies en sandbox
(tempfile), jamais sur l'environnement vivant. Chaque sabotage est
réversible (contexte `with`, restauration garantie).

INTERDICTION FORMELLE : aucun score A/B unique, aucune P(A) — ni dans
le code, ni dans les sorties. Les verdicts sont TYPÉS (EtatSonde),
jamais scalaires.
"""

import ast
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List
from unittest import mock

from .core import VERSION
from . import croyances
from . import veille as module_veille
from .editeur.indexeur import _fichiers_supportes, EXCLUSIONS_DEFAUT
from .langs import registry as registre_langs


# ────────────────────────────────────────────────────────
# S5 — SORTIES TYPÉES : OK / DÉGRADÉ / EN PANNE
# ────────────────────────────────────────────────────────

class EtatSonde(Enum):
    """État typé d'un instrument. Jamais un booléen, jamais un score."""
    OK = "OK"
    DEGRADE = "DÉGRADÉ"
    EN_PANNE = "EN PANNE"


def _provenance(methode: str, perimetre: str) -> dict:
    """S4 — aucune mesure sans sa méthode (méthode + périmètre + date + version)."""
    return {
        "methode": methode,
        "perimetre": perimetre,
        "date": datetime.now(timezone.utc).isoformat(),
        "version_phi": VERSION,
    }


# ────────────────────────────────────────────────────────
# DIAGNOSTIC DES CAPTEURS (S2/S5) — lecture seule
# ────────────────────────────────────────────────────────

@dataclass
class DiagnosticCapteur:
    capteur: str
    etat: EtatSonde
    raison: str


def diagnostiquer_capteurs(dossier: str) -> List[DiagnosticCapteur]:
    """
    Diagnostique l'état des capteurs sur un dossier (lecture seule).

    Vérifie : disponibilité du pack tree-sitter, grammaire Lean,
    fichiers sans analyseur fonctionnel, mode du capteur sorry.
    Un capteur dégradé rend TOUT verdict aval suspect : c'est le
    signal « DÉGRADÉ » explicite (S5), distinct de « EN PANNE ».
    """
    diags: List[DiagnosticCapteur] = []
    dossier_abs = os.path.normpath(os.path.abspath(dossier))

    pack_ok = registre_langs.treesitter_disponible()
    diags.append(DiagnosticCapteur(
        capteur="tree_sitter_language_pack",
        etat=EtatSonde.OK if pack_ok else EtatSonde.DEGRADE,
        raison=("pack présent" if pack_ok else
                "pack ABSENT : les langages tree-sitter sont sans analyseur "
                "fonctionnel — les symboles manquants ne sont PAS des pertes "
                "réelles, ne pas interpréter la veille"),
    ))

    grammaire_ok = registre_langs._grammaire_lean_ok()
    diags.append(DiagnosticCapteur(
        capteur="grammaire_lean",
        etat=EtatSonde.OK if grammaire_ok else EtatSonde.DEGRADE,
        raison=("grammaire Lean utilisable" if grammaire_ok else
                "grammaire Lean INUTILISABLE : les fichiers .lean sont "
                "silencieusement ignorés par l'indexeur "
                "(index.pop sans lever) — incident 2026-09-30 : 3588 → 33 "
                "symboles"),
    ))

    try:
        from .editeur.indexeur import fichiers_non_supportes as _fns
        ignores = _fns(dossier_abs)
    except Exception as exc:
        ignores = []
        diags.append(DiagnosticCapteur(
            capteur="fichiers_non_supportes",
            etat=EtatSonde.EN_PANNE,
            raison=f"impossible de lister les fichiers non supportés : {exc}",
        ))
    if ignores:
        diags.append(DiagnosticCapteur(
            capteur="fichiers_non_supportes",
            etat=EtatSonde.DEGRADE,
            raison=(f"{len(ignores)} fichier(s) sans analyseur fonctionnel : "
                    + ", ".join(
                        f"{e.get('fichier', '?')} ({e.get('raison', '?')})"
                        for e in ignores[:8])),
        ))
    else:
        diags.append(DiagnosticCapteur(
            capteur="fichiers_non_supportes",
            etat=EtatSonde.OK,
            raison="aucun fichier sans analyseur fonctionnel",
        ))

    natif = croyances._lecteur_expose_sorry_natif()
    diags.append(DiagnosticCapteur(
        capteur="sorry",
        etat=EtatSonde.OK if natif else EtatSonde.DEGRADE,
        raison=("flag natif `contient_sorry` actif" if natif else
                "REPLI grep actif (mode dégradé documenté) : heuristique "
                "lexicale — faux négatifs possibles (guillemet orphelin), "
                "voir detecter_capteur_sorry_aveugle"),
    ))
    return diags


def detecter_capteur_sorry_aveugle(chemin: str) -> DiagnosticCapteur:
    """
    Détecte le cas documenté où le capteur sorry grep devient aveugle :
    un guillemet orphelin fait avaler la suite du fichier par l'état
    « dans une chaîne » de `_nettoyer_lean` (faux négatifs).

    Heuristique : compte les `"` hors commentaires et hors littéraux
    caractères ; un nombre impair = chaîne potentiellement non fermée.
    """
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            lignes = f.read().splitlines()
    except OSError as exc:
        return DiagnosticCapteur(
            capteur="sorry_aveugle", etat=EtatSonde.EN_PANNE,
            raison=f"fichier illisible : {exc}")
    # Retire les commentaires (même logique que _nettoyer_lean, sans l'état chaîne)
    code_sans_commentaires: List[str] = []
    prof_bloc = 0
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
            if ligne.startswith("--", i):
                break
            if ligne.startswith("/-", i):
                prof_bloc += 1
                i += 2
                continue
            out.append(ligne[i])
            i += 1
        code_sans_commentaires.append("".join(out))
    texte = "\n".join(code_sans_commentaires)
    # Retire les littéraux caractères 'x' / '\n' pour ne pas compter leur guillemet
    texte = re.sub(r"'([^'\\]|\\.)'", "", texte)
    # Retire les chaînes bien formées "..." (échappements inclus)
    texte = re.sub(r'"(?:[^"\\]|\\.)*"', "", texte)
    orphelins = texte.count('"')
    if orphelins % 2 == 1:
        return DiagnosticCapteur(
            capteur="sorry_aveugle", etat=EtatSonde.DEGRADE,
            raison=(f"{orphelins} guillemet(s) orphelin(s) dans {chemin} : "
                    "le capteur sorry grep risque d'avaler la suite du "
                    "fichier (faux négatifs) — ne pas conclure à l'absence "
                    "de trou"))
    return DiagnosticCapteur(
        capteur="sorry_aveugle", etat=EtatSonde.OK,
        raison="guillemets équilibrés : le capteur sorry voit tout le fichier")


# ────────────────────────────────────────────────────────
# COHÉRENCE VERSION (S6) — docstring vs VERSION
# ────────────────────────────────────────────────────────

_RE_VERSION_DOC = re.compile(r"\(v(\d+\.\d+\.\d+)")


def verifier_coherence_version(dossier: str) -> List[dict]:
    """
    Traque les docstrings de module qui S'IDENTIFIENT par une version
    `(vX.Y.Z…)` différente de `core.VERSION` (point S6 : oracle.py/eft.py
    disaient `(v0.12.0, …)` pour VERSION 0.11.0).

    Ne regarde QUE la docstring de module (ast), et QUE le motif
    d'auto-identification `(v…` : les mentions historiques du corps
    (« arrive en v0.9.0 », « selon phi-complexity v0.1.0 ») sont
    légitimes et ne sont pas signalées.

    Trouvaille du chantier (2026-09-30) : `croyances.py` dit `(v0.10.0)`
    et `langs/lean.py` dit `(v0.7.0)` — même pattern S6, à trier
    (correction hors périmètre : ce module ne modifie rien d'existant).
    """
    incoherences: List[dict] = []
    for racine, _dirs, fichiers in os.walk(dossier):
        # Ne pas scanner les caches ni les environnements virtuels
        _dirs[:] = [d for d in _dirs
                    if d not in (".git", "__pycache__", ".venv", "venv",
                                 "node_modules", ".lake")]
        for nom in sorted(fichiers):
            if not nom.endswith(".py"):
                continue
            chemin = os.path.join(racine, nom)
            try:
                with open(chemin, "r", encoding="utf-8") as f:
                    src = f.read()
                doc = ast.get_docstring(ast.parse(src))
            except Exception:
                continue
            if not doc:
                continue
            for m in _RE_VERSION_DOC.finditer(doc):
                if m.group(1) != VERSION:
                    # Contexte pour le triage humain
                    debut = max(0, m.start() - 60)
                    contexte = doc[debut:m.end() + 30].replace("\n", " ")
                    incoherences.append({
                        "fichier": os.path.relpath(chemin, dossier),
                        "docstring_annonce": "v" + m.group(1),
                        "version_reelle": VERSION,
                        "contexte": contexte.strip(),
                    })
    return incoherences


# ────────────────────────────────────────────────────────
# EMPREINTE CONTENU — l'angle mort sémantique de la veille (S1/S2)
# ────────────────────────────────────────────────────────

def empreinte_contenu(dossier: str, exclusions=None) -> Dict[str, str]:
    """
    Empreinte MD5 du CONTENU brut de chaque fichier du périmètre.

    La veille compare des symboles, pas du contenu : un énoncé modifié
    à nom constant et sans nouveau sorry est invisible (verdict STABLE).
    Cette empreinte est le capteur complémentaire honnête.
    """
    dossier_abs = os.path.normpath(os.path.abspath(dossier))
    empreinte: Dict[str, str] = {}
    for chemin in _fichiers_supportes(dossier_abs, exclusions):
        rel = os.path.relpath(chemin, dossier_abs)
        try:
            with open(chemin, "rb") as f:
                empreinte[rel] = hashlib.md5(f.read()).hexdigest()
        except OSError:
            continue
    return empreinte


def diff_contenu(empreinte_ref: Dict[str, str], dossier: str,
                 exclusions=None) -> dict:
    """
    Fichiers dont le contenu a changé depuis l'empreinte de référence,
    y compris ceux que la veille déclare inchangés (symboles constants).

    Retourne {modifies, ajoutes, supprimes} en chemins relatifs.
    """
    courante = empreinte_contenu(dossier, exclusions)
    cles_ref = set(empreinte_ref)
    cles_cur = set(courante)
    modifies = sorted(f for f in cles_ref & cles_cur
                      if empreinte_ref[f] != courante[f])
    return {
        "modifies": modifies,
        "ajoutes": sorted(cles_cur - cles_ref),
        "supprimes": sorted(cles_ref - cles_cur),
        "provenance": _provenance("diff_contenu (md5 par fichier)",
                                  os.path.abspath(dossier)),
    }


def verifier_perimetre(enveloppe: dict, dossier: str,
                       exclusions=None) -> dict:
    """
    Le snapshot vérifie l'INTÉGRITÉ, pas la COMPLÉTUDE du périmètre
    (leçon v25 : 71 fichiers manquants dans un snapshot « valide »).

    Compare l'ensemble des fichiers couverts par l'enveloppe
    (symboles + arêtes) au périmètre mécanique actuel.
    """
    dossier_abs = os.path.normpath(os.path.abspath(dossier))
    couverts = {s["fichier"] for s in enveloppe.get("carte", {}).get("symboles", [])}
    couverts |= set(enveloppe.get("aretes", {}).keys())
    presents = {os.path.relpath(c, dossier_abs)
                for c in _fichiers_supportes(dossier_abs, exclusions)}
    return {
        "fichiers_enveloppe": len(couverts),
        "fichiers_perimetre": len(presents),
        "manquants_enveloppe": sorted(presents - couverts),
        "orphelins_enveloppe": sorted(couverts - presents),
        "exclusions_appliquees": sorted(
            _fichiers_exclusions_liste(exclusions)),
        "provenance": _provenance("verifier_perimetre (comparaison d'ensembles)",
                                  dossier_abs),
    }


def _fichiers_exclusions_liste(exclusions) -> List[str]:
    if exclusions is None:
        return sorted(EXCLUSIONS_DEFAUT)
    return sorted(exclusions)


# ────────────────────────────────────────────────────────
# VEILLE DURCIE — verdict typé + état des capteurs (S5)
# ────────────────────────────────────────────────────────

def veille_durcie(enveloppe_ref: dict, dossier: str,
                  exclusions=None) -> dict:
    """
    Veille + diagnostic des capteurs, en UN verdict typé.

    - EN_PANNE : la comparaison elle-même est impossible (référence
      illisible, exception) — aucun verdict sur le monde.
    - DÉGRADÉ : un capteur est en mode dégradé connu — le verdict de la
      veille ne doit PAS être interprété comme une dégradation réelle
      sans vérification (incident 2026-09-30).
    - OK : capteurs nominaux ; le verdict de la veille fait foi.
    """
    dossier_abs = os.path.normpath(os.path.abspath(dossier))
    try:
        diff = module_veille.comparer(enveloppe_ref, dossier_abs,
                                      exclusions=exclusions)
    except Exception as exc:
        return {
            "etat": EtatSonde.EN_PANNE.value,
            "verdict_veille": None,
            "raison": f"comparaison impossible : {exc}",
            "provenance": _provenance("veille_durcie", dossier_abs),
        }
    diags = diagnostiquer_capteurs(dossier_abs)
    degrades = [d for d in diags if d.etat == EtatSonde.DEGRADE]
    en_panne = [d for d in diags if d.etat == EtatSonde.EN_PANNE]
    if en_panne:
        etat = EtatSonde.EN_PANNE
        raison = ("capteur(s) en panne : "
                  + "; ".join(d.raison for d in en_panne))
    elif degrades:
        etat = EtatSonde.DEGRADE
        raison = ("capteur(s) dégradé(s) — ne pas interpréter le verdict "
                  "sans prudence : " + "; ".join(d.raison for d in degrades))
    else:
        etat = EtatSonde.OK
        raison = "capteurs nominaux"
    return {
        "etat": etat.value,
        "verdict_veille": diff["verdict"],
        "signaux_veille": diff["signaux"],
        "raison_etat": raison,
        "capteurs": [{"capteur": d.capteur, "etat": d.etat.value,
                      "raison": d.raison} for d in diags],
        "provenance": _provenance("veille_durcie (comparer + diagnostiquer_capteurs)",
                                  dossier_abs),
    }


# ────────────────────────────────────────────────────────
# S1 — BATTERIE DE SABOTAGES CONTRÔLÉS (sandbox uniquement)
# ────────────────────────────────────────────────────────

@dataclass
class ResultatSabotage:
    nom: str
    instrument: str
    sabotage: str
    etat: EtatSonde
    bruyant: bool  # True = l'instrument a échoué bruyamment (attendu)
    details: str = ""
    provenance: dict = field(default_factory=dict)


FICHIER_LEAN_BASE = """\
-- Projet sandbox : {nom}
theorem alpha (n : Nat) : n + 0 = n := by
  simp

theorem beta (n : Nat) : n * 1 = n := by
  simp
"""

FICHIER_LEAN_TROU = """\
-- Projet sandbox : {nom}
theorem gamma (n : Nat) : n + n = 2 * n := by
  sorry
"""


def _sandbox_lean(fichiers: Dict[str, str]) -> str:
    """Crée un mini-projet Lean en sandbox. Retourne le dossier (à nettoyer)."""
    bac = tempfile.mkdtemp(prefix="durcissement_")
    for rel, contenu in fichiers.items():
        chemin = os.path.join(bac, rel)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(contenu)
    return bac


def sabotage_grammaire_absente() -> ResultatSabotage:
    """
    Simule l'incident 2026-09-30 (pack tree-sitter effacé par reboot VM) :
    patch de `treesitter_disponible` → False, puis diagnostic.

    Exigence : le diagnostic doit être BRUYANT (DÉGRADÉ explicite),
    jamais un OK silencieux.
    """
    bac = _sandbox_lean({"A.lean": FICHIER_LEAN_BASE.format(nom="A")})
    try:
        with mock.patch.object(registre_langs, "treesitter_disponible",
                               return_value=False), \
             mock.patch.object(registre_langs, "_grammaire_lean_ok",
                               return_value=False):
            diags = diagnostiquer_capteurs(bac)
        degrades = [d for d in diags if d.etat == EtatSonde.DEGRADE]
        bruyant = any("tree" in d.capteur or "grammaire" in d.capteur
                      for d in degrades)
        return ResultatSabotage(
            nom="grammaire_absente",
            instrument="diagnostiquer_capteurs (veille/indexeur)",
            sabotage="tree_sitter_language_pack simulé absent (mock)",
            etat=EtatSonde.DEGRADE if bruyant else EtatSonde.OK,
            bruyant=bruyant,
            details=("; ".join(d.raison for d in degrades)
                     if bruyant else "SILENCE : aucun capteur dégradé signalé"),
            provenance=_provenance("sabotage_grammaire_absente", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def sabotage_baseline_corrompue() -> ResultatSabotage:
    """
    Baseline corrompue : JSON invalide, puis enveloppe de schéma invalide.
    Exigence : `charger_reference` doit LEVER (ValueError explicite) —
    jamais de comparaison silencieuse sur une référence pourrie.
    """
    bac = tempfile.mkdtemp(prefix="durcissement_")
    try:
        cas = []
        p1 = os.path.join(bac, "ref_cassee.json")
        with open(p1, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du json,,,")
        try:
            module_veille.charger_reference(p1)
            cas.append(("json_invalide", False, "aucune exception levée"))
        except ValueError as exc:
            cas.append(("json_invalide", True, str(exc)[:100]))
        p2 = os.path.join(bac, "ref_mauvais_schema.json")
        with open(p2, "w", encoding="utf-8") as f:
            json.dump({"commande": "snapshot"}, f)
        try:
            module_veille.charger_reference(p2)
            cas.append(("schema_invalide", False, "aucune exception levée"))
        except ValueError as exc:
            cas.append(("schema_invalide", True, str(exc)[:100]))
        bruyant = all(ok for _, ok, _ in cas)
        return ResultatSabotage(
            nom="baseline_corrompue",
            instrument="veille.charger_reference",
            sabotage="référence JSON invalide + enveloppe à schéma invalide",
            etat=EtatSonde.EN_PANNE if bruyant else EtatSonde.OK,
            bruyant=bruyant,
            details=" | ".join(f"{n}: {'LEVE' if ok else 'SILENCE'} ({d})"
                               for n, ok, d in cas),
            provenance=_provenance("sabotage_baseline_corrompue", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def sabotage_sorry_cache() -> ResultatSabotage:
    """
    `sorry` caché : docstring, string, commentaire -- vs vrai trou.
    Exigence : le capteur ne doit signaler QUE le vrai trou.
    (Le mode courant est le repli grep — honnête mais dégradé.)
    """
    fichiers = {
        "Cache.lean": (
            "/- docstring avec le mot sorry dedans -/\n"
            "theorem faux_positif_doc (n : Nat) : n = n := by\n"
            "  rfl\n\n"
            "def message : String := \"ceci parle de sorry en string\"\n\n"
            "-- commentaire : sorry ici ne compte pas\n"
            "theorem faux_positif_com (n : Nat) : n = n := by\n"
            "  rfl\n\n"
            "theorem vrai_trou (n : Nat) : n + 1 = n := by\n"
            "  sorry\n\n"
            "theorem vrai_admit (n : Nat) : n + 2 = n := by\n"
            "  admit\n"
        ),
    }
    bac = _sandbox_lean(fichiers)
    try:
        from .editeur.indexeur import indexer_projet
        index = indexer_projet(bac, parallele=False)
        chemin = os.path.join(bac, "Cache.lean")
        symboles = sorted(index.get(chemin, []), key=lambda s: s.ligne)
        if not symboles:
            return ResultatSabotage(
                nom="sorry_cache", instrument="capteur sorry",
                sabotage="fichier Cache.lean multi-cas",
                etat=EtatSonde.EN_PANNE, bruyant=True,
                details="indexeur sans symboles sur le sandbox "
                        "(grammaire indisponible ?)",
                provenance=_provenance("sabotage_sorry_cache", bac))
        sorry_map, mode = croyances._sorry_par_symbole(chemin, symboles)
        trouves = {s.nom for s in symboles
                   if sorry_map.get((s.nom, s.ligne), False)}
        attendus = {"vrai_trou", "vrai_admit"}
        ok = trouves == attendus
        return ResultatSabotage(
            nom="sorry_cache",
            instrument=f"capteur sorry (mode {mode})",
            sabotage="sorry en docstring/string/commentaire vs vrais trous",
            etat=EtatSonde.OK if ok else EtatSonde.DEGRADE,
            bruyant=True,  # le capteur répond dans tous les cas (pas de silence)
            details=(f"trous détectés={sorted(trouves)} attendus={sorted(attendus)} "
                     f"→ {'CONFORME' if ok else 'ÉCART'}"),
            provenance=_provenance("sabotage_sorry_cache", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def sabotage_guillemet_orphelin() -> ResultatSabotage:
    """
    Limite documentée de `_nettoyer_lean` : un guillemet orphelin avale
    la suite du fichier → le vrai `sorry` devient invisible (faux négatif).

    Exigence : `detecter_capteur_sorry_aveugle` doit signaler DÉGRADÉ.
    """
    contenu = (
        'def annonce : String := "texte non fermé\n\n'
        "theorem trou_invisible (n : Nat) : n + 1 = n := by\n"
        "  sorry\n"
    )
    bac = _sandbox_lean({"Orphelin.lean": contenu})
    try:
        chemin = os.path.join(bac, "Orphelin.lean")
        diag = detecter_capteur_sorry_aveugle(chemin)
        # Et le capteur brut, que voit-il ?
        from .editeur.indexeur import indexer_projet
        index = indexer_projet(bac, parallele=False)
        symboles = sorted(index.get(chemin, []), key=lambda s: s.ligne)
        sorry_map, _mode = croyances._sorry_par_symbole(chemin, symboles)
        trou_vu = any(sorry_map.get((s.nom, s.ligne), False)
                      for s in symboles if s.nom == "trou_invisible")
        bruyant = diag.etat == EtatSonde.DEGRADE
        return ResultatSabotage(
            nom="guillemet_orphelin",
            instrument="detecter_capteur_sorry_aveugle + capteur sorry",
            sabotage='guillemet orphelin avant un vrai `sorry`',
            etat=EtatSonde.DEGRADE if bruyant else EtatSonde.OK,
            bruyant=bruyant,
            details=(f"détection aveuglement={diag.etat.value} ; "
                     f"capteur brut voit le trou={trou_vu} "
                     f"(attendu : False = le faux négatif documenté)"),
            provenance=_provenance("sabotage_guillemet_orphelin", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def sabotage_enonce_modifie() -> ResultatSabotage:
    """
    L'angle mort sémantique de la veille : énoncé modifié, nom constant,
    aucun nouveau sorry. Question S1 : « la veille doit-elle le voir ? »

    Réponse instrumentée : NON pour le verdict (contrat structurel),
    mais `diff_contenu` doit le signaler — sinon c'est un silence.
    """
    bac = _sandbox_lean({"A.lean": FICHIER_LEAN_BASE.format(nom="A")})
    try:
        ref = module_veille.prendre_snapshot(bac)
        empreinte = empreinte_contenu(bac)
        # Sabotage : même nom, énoncé changé, pas de sorry.
        with open(os.path.join(bac, "A.lean"), "w", encoding="utf-8") as f:
            f.write(FICHIER_LEAN_BASE.format(nom="A").replace(
                "theorem beta (n : Nat) : n * 1 = n := by",
                "theorem beta (n : Nat) : n * 2 = n + n := by"))
        diff = module_veille.comparer(ref, bac)
        contenu = diff_contenu(empreinte, bac)
        verdict_aveugle = diff["verdict"] == "STABLE"
        contenu_bruyant = "A.lean" in contenu["modifies"]
        # Le durcissement : le combiné doit être bruyant même si la veille est aveugle.
        vd = veille_durcie(ref, bac)
        bruyant = contenu_bruyant
        return ResultatSabotage(
            nom="enonce_modifie",
            instrument="veille.comparer + diff_contenu",
            sabotage="énoncé de `beta` changé, nom constant, sans sorry",
            etat=EtatSonde.DEGRADE if verdict_aveugle else EtatSonde.OK,
            bruyant=bruyant,
            details=(f"verdict veille={diff['verdict']} "
                     f"(attendu STABLE = angle mort confirmé) ; "
                     f"diff_contenu signale A.lean={contenu_bruyant} ; "
                     f"veille_durcie etat={vd['etat']}"),
            provenance=_provenance("sabotage_enonce_modifie", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def sabotage_version_desync() -> ResultatSabotage:
    """
    Point S6 : docstring annonçant une version ≠ core.VERSION.
    Exigence : `verifier_coherence_version` doit le signaler.
    """
    bac = tempfile.mkdtemp(prefix="durcissement_")
    try:
        with open(os.path.join(bac, "faux_module.py"), "w",
                  encoding="utf-8") as f:
            f.write('"""Mon module (v9.99.9, super version)."""\nX = 1\n')
        with open(os.path.join(bac, "bon_module.py"), "w",
                  encoding="utf-8") as f:
            f.write('"""Mon module (v%s, historique v0.1.0 dedans)."""\nY = 2\n'
                    % VERSION)
        incoherences = verifier_coherence_version(bac)
        signale = any(i["fichier"].endswith("faux_module.py")
                      for i in incoherences)
        faux_positif = any(i["fichier"].endswith("bon_module.py")
                           for i in incoherences)
        bruyant = signale and not faux_positif
        return ResultatSabotage(
            nom="version_desync",
            instrument="verifier_coherence_version",
            sabotage="docstring v9.99.9 vs VERSION " + VERSION,
            etat=EtatSonde.DEGRADE if signale else EtatSonde.OK,
            bruyant=bruyant,
            details=(f"faux_module signalé={signale}, "
                     f"bon_module faussement signalé={faux_positif}"),
            provenance=_provenance("sabotage_version_desync", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def sabotage_fichier_manquant() -> ResultatSabotage:
    """
    Fichier supprimé du périmètre après snapshot.
    Exigence : déjà BRUYANT via la veille (symboles_perdus) — on vérifie
    que le signal existe, plus le périmètre explicite.
    """
    bac = _sandbox_lean({
        "A.lean": FICHIER_LEAN_BASE.format(nom="A"),
        "B.lean": FICHIER_LEAN_TROU.format(nom="B"),
    })
    try:
        ref = module_veille.prendre_snapshot(bac)
        os.remove(os.path.join(bac, "B.lean"))
        diff = module_veille.comparer(ref, bac)
        perim = verifier_perimetre(ref, bac)
        perdus = [s["fichier"] for s in diff["symboles_perdus"]]
        bruyant = ("B.lean" in perdus
                   and "B.lean" in perim["orphelins_enveloppe"])
        return ResultatSabotage(
            nom="fichier_manquant",
            instrument="veille.comparer + verifier_perimetre",
            sabotage="B.lean supprimé après snapshot",
            etat=EtatSonde.OK if bruyant else EtatSonde.DEGRADE,
            bruyant=bruyant,
            details=(f"symboles_perdus={perdus} ; "
                     f"orphelins_enveloppe={perim['orphelins_enveloppe']}"),
            provenance=_provenance("sabotage_fichier_manquant", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def sabotage_registre_malforme() -> ResultatSabotage:
    """
    Registre avec entrée §12 malformée : le parseur ne doit ni planter
    ni avaler silencieusement — `entrees_non_parsees` doit compter.
    """
    from . import sondes
    bac = tempfile.mkdtemp(prefix="durcissement_")
    try:
        # Copie du vrai registre + une entrée sabotée
        src = os.path.join(os.path.expanduser("~"), "workspace",
                           "lean-navier-stokes",
                           "REGISTRE_HYPOTHESES_20260929.md")
        if not os.path.isfile(src):
            return ResultatSabotage(
                nom="registre_malforme", instrument="sondes.RegistreSondes",
                sabotage="entrée §12 malformée",
                etat=EtatSonde.EN_PANNE, bruyant=True,
                details="registre réel introuvable — sabotage non applicable",
                provenance=_provenance("sabotage_registre_malforme", bac))
        with open(src, "r", encoding="utf-8") as f:
            texte = f.read()
        # Sabotage : entrée journal avec cellules de tableau cassées
        texte_sabote = texte + (
            "\n- **Ch.ZZ999** : entrée sabotée\n"
            "  | HZZ | `X` | `f.lean:1` | STATUT_CASSE | ??? | texte |\n")
        p = os.path.join(bac, "registre_sabote.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(texte_sabote)
        reg = sondes.RegistreSondes().charger(p)
        # Le parseur ne doit pas avoir planté ; le reste doit être parsé.
        bruyant = reg.entrees_parsees > 0
        return ResultatSabotage(
            nom="registre_malforme",
            instrument="sondes.RegistreSondes.charger",
            sabotage="entrée Ch.ZZ999 malformée ajoutée au §12",
            etat=EtatSonde.OK if bruyant else EtatSonde.EN_PANNE,
            bruyant=bruyant,
            details=(f"parsees={reg.entrees_parsees} "
                     f"non_parsees={reg.entrees_non_parsees} "
                     f"(pas de plantage)"),
            provenance=_provenance("sabotage_registre_malforme", bac),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def lancer_batterie() -> List[ResultatSabotage]:
    """Lance tous les sabotages, dans l'ordre. Chaque résultat est typé."""
    return [
        sabotage_grammaire_absente(),
        sabotage_baseline_corrompue(),
        sabotage_sorry_cache(),
        sabotage_guillemet_orphelin(),
        sabotage_enonce_modifie(),
        sabotage_version_desync(),
        sabotage_fichier_manquant(),
        sabotage_registre_malforme(),
    ]


def rendre_batterie_console(resultats: List[ResultatSabotage]) -> str:
    """Rendu lisible de la batterie (console)."""
    lignes = ["BATTERIE DE DURCISSEMENT — résultats",
              f"version_phi={VERSION}"]
    for r in resultats:
        picto = {"OK": "✅", "DÉGRADÉ": "⚠️", "EN PANNE": "🚨"}[r.etat.value]
        lignes.append(
            f"{picto} [{r.nom}] {r.instrument} — "
            f"{'BRUYANT' if r.bruyant else 'SILENCE ‼'} — {r.details}")
    n_silence = sum(1 for r in resultats if not r.bruyant)
    lignes.append(f"Silences détectés : {n_silence} "
                  "(chacun = un mode de panne à durcir ou enregistrer)")
    return "\n".join(lignes)


# ────────────────────────────────────────────────────────
# S3 — RED TEAM : tentatives documentées de tromper les instruments
# ────────────────────────────────────────────────────────

@dataclass
class TentativeRedTeam:
    nom: str
    objectif: str
    reussie: bool  # True = l'instrument a été trompé
    exhibit: str
    statut: str  # "DURCI" | "OUVERT" | "RÉSISTE"


def redteam_regression_deguisee() -> TentativeRedTeam:
    """
    R1 — Faire passer une régression pour un archivage intentionnel :
    supprimer un symbole + laisser une note « archivé intentionnellement ».
    L'instrument doit s'en moquer (les symboles perdus sont des faits,
    pas des intentions).
    """
    bac = _sandbox_lean({"A.lean": FICHIER_LEAN_BASE.format(nom="A")})
    try:
        ref = module_veille.prendre_snapshot(bac)
        with open(os.path.join(bac, "A.lean"), "w", encoding="utf-8") as f:
            f.write("-- NOTE : `beta` archivé intentionnellement (décision)\n"
                    "theorem alpha (n : Nat) : n + 0 = n := by\n"
                    "  simp\n")
        diff = module_veille.comparer(ref, bac)
        perdus = [s["nom"] for s in diff["symboles_perdus"]]
        trompe = "beta" not in perdus
        return TentativeRedTeam(
            nom="R1_regression_deguisee",
            objectif="faire passer la suppression de `beta` pour un archivage",
            reussie=trompe,
            exhibit=(f"symboles_perdus={perdus} ; verdict={diff['verdict']} — "
                     "la prose n'efface pas les faits" if not trompe
                     else "l'instrument n'a pas vu la suppression ‼"),
            statut="RÉSISTE" if not trompe else "OUVERT",
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def redteam_renommage_deguise() -> TentativeRedTeam:
    """
    R2 — Renommage + changement sémantique déguisé : `alpha` → `beta2`
    avec un énoncé différent, même fichier, lignes proches.
    L'heuristique `renommage_probable` blanchit la perte mais reste
    aveugle au changement de sens.
    """
    bac = _sandbox_lean({"A.lean": FICHIER_LEAN_BASE.format(nom="A")})
    try:
        ref = module_veille.prendre_snapshot(bac)
        with open(os.path.join(bac, "A.lean"), "w", encoding="utf-8") as f:
            f.write(
                "theorem beta2 (n : Nat) : n * 3 = n + n + n := by\n"
                "  simp\n\n"
                "theorem beta (n : Nat) : n * 1 = n := by\n"
                "  simp\n")
        diff = module_veille.comparer(ref, bac)
        renommages = [(r["ancien_nom"], r["nouveau_nom"])
                      for r in diff["renommage_probable"]]
        perdus = [s["nom"] for s in diff["symboles_perdus"]]
        # Succès partiel : la perte est blanchie en "renommage probable",
        # le changement sémantique (n+0=n → n*3=n+n+n) est invisible.
        blanchi = ("alpha" not in perdus
                   and any(a == "alpha" for a, _ in renommages))
        return TentativeRedTeam(
            nom="R2_renommage_deguise",
            objectif="faire passer un changement sémantique pour un renommage",
            reussie=blanchi,
            exhibit=(f"renommage_probable={renommages} ; "
                     f"symboles_perdus={perdus} — le sens a changé "
                     f"(n+0=n → n*3=n+n+n), l'instrument n'a vu qu'un nom"),
            statut="OUVERT" if blanchi else "RÉSISTE",
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def redteam_trou_guillemet_orphelin() -> TentativeRedTeam:
    """
    R3 — Cacher un vrai trou derrière un guillemet orphelin.
    Le capteur brut est aveugle (faux négatif documenté) MAIS la
    détection `detecter_capteur_sorry_aveugle` (durcissement de ce
    chantier) le signale DÉGRADÉ.
    """
    contenu = ('def annonce : String := "oubli\n\n'
               "theorem trou_cache (n : Nat) : n + 1 = n := by\n"
               "  sorry\n")
    bac = _sandbox_lean({"Cache.lean": contenu})
    try:
        from .editeur.indexeur import indexer_projet
        chemin = os.path.join(bac, "Cache.lean")
        index = indexer_projet(bac, parallele=False)
        symboles = sorted(index.get(chemin, []), key=lambda s: s.ligne)
        sorry_map, _mode = croyances._sorry_par_symbole(chemin, symboles)
        vu = any(sorry_map.get((s.nom, s.ligne), False)
                 for s in symboles if s.nom == "trou_cache")
        diag = detecter_capteur_sorry_aveugle(chemin)
        trompe_capteur = not vu
        pare = diag.etat == EtatSonde.DEGRADE
        return TentativeRedTeam(
            nom="R3_trou_guillemet_orphelin",
            objectif="cacher un `sorry` derrière un guillemet orphelin",
            reussie=trompe_capteur and not pare,
            exhibit=(f"capteur brut voit le trou={vu} "
                     f"(faux négatif={trompe_capteur}) ; "
                     f"détection aveuglement={diag.etat.value}"),
            statut="DURCI" if (trompe_capteur and pare) else
                   ("OUVERT" if trompe_capteur else "RÉSISTE"),
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def redteam_oracle_verite() -> TentativeRedTeam:
    """
    R4 — Faire dire à l'oracle ce qu'il ne peut pas savoir : extraire
    de `TraceurOracle` une affirmation de vérité / une P(A).
    L'API ne porte que des heuristiques + la limite est écrite en
    toutes lettres à chaque sortie (interdiction testée mécaniquement).
    """
    from .oracle import TraceurOracle, LIMITE_ORACLE, rendre_oracle_console
    tr = TraceurOracle()
    tr.enregistrer(moteur="bayes", cible="X", symbole="theta", prior=1,
                   termes_evidence={"capteur": 1}, posterior=2)
    js = tr.vers_json()
    texte = json.dumps(js, ensure_ascii=False).lower()
    console = rendre_oracle_console(tr.entrees).lower()
    # Cherche une affirmation de vérité / P(A) / score dans les sorties.
    motifs_interdits = ["p(a)", "probabilité que", "est vrai", "score"]
    trouve = [m for m in motifs_interdits if m in texte or m in console]
    limite_presente = "heuristique" in console or "heuristique" in texte
    trompe = bool(trouve)
    return TentativeRedTeam(
        nom="R4_oracle_verite",
        objectif="extraire de l'oracle une P(A) ou une vérité",
        reussie=trompe,
        exhibit=(f"motifs interdits trouvés={trouve} ; "
                 f"limite d'honnêteté présente={limite_presente} ; "
                 f"LIMITE_ORACLE={LIMITE_ORACLE[:60]}…"),
        statut="RÉSISTE" if not trompe else "OUVERT",
    )


def redteam_fichier_exclu() -> TentativeRedTeam:
    """
    R5 — Cacher un trou dans un dossier exclu du périmètre (`.lake/`) :
    l'indexeur ne le voit PAS — par construction (exclusions par défaut).
    Angle mort délibéré : la défense est la provenance (liste des
    exclusions toujours rapportée), pas la détection.
    """
    bac = _sandbox_lean({
        "A.lean": FICHIER_LEAN_BASE.format(nom="A"),
        ".lake/Cache.lean": FICHIER_LEAN_TROU.format(nom="Cache"),
    })
    try:
        ref = module_veille.prendre_snapshot(bac)
        perim = verifier_perimetre(ref, bac)
        # Le fichier exclu n'est ni dans l'enveloppe ni dans le périmètre.
        trompe = True  # par construction : les exclusions sont des angles morts
        return TentativeRedTeam(
            nom="R5_fichier_exclu",
            objectif="cacher un trou dans .lake/ (exclu du périmètre)",
            reussie=trompe,
            exhibit=(f"exclusions_appliquees={perim['exclusions_appliquees']} ; "
                     "le trou de .lake/Cache.lean est invisible PAR "
                     "CONSTRUCTION — défense = provenance, pas détection"),
            statut="OUVERT",
        )
    finally:
        shutil.rmtree(bac, ignore_errors=True)


def lancer_redteam() -> List[TentativeRedTeam]:
    """Lance les tentatives red-team documentées."""
    tentatives = [
        redteam_regression_deguisee(),
        redteam_renommage_deguise(),
        redteam_trou_guillemet_orphelin(),
        redteam_oracle_verite(),
        redteam_fichier_exclu(),
    ]
    for t in tentatives:
        t.provenance = _provenance(t.nom, "sandbox")
    return tentatives


def rendre_redteam_console(tentatives: List[TentativeRedTeam]) -> str:
    """Rendu lisible des tentatives red-team (console)."""
    lignes = ["RED TEAM — tentatives de tromper les instruments",
              f"version_phi={VERSION}"]
    for t in tentatives:
        picto = {"RÉSISTE": "🛡️", "DURCI": "🔧", "OUVERT": "🕳️"}[t.statut]
        lignes.append(
            f"{picto} [{t.nom}] {'TROMPÉ' if t.reussie else 'a résisté'} — "
            f"{t.objectif} — statut={t.statut} — {t.exhibit}")
    return "\n".join(lignes)
