"""
journal.py — Journal d'activité et empreinte résiduelle (2026-10-05).

Idée de Tomy : autour des agents, des mécanismes de gestion des
exceptions — comme les logiciels de reconstruction de données — qui
impriment mécaniquement, en pleine défaillance, l'empreinte résiduelle
des activités en cours. Devoir des gardiens : protéger les agents et
leurs contenus.

Ce module est la version honnête et interne de cette idée :

1. JOURNAL D'ACTIVITÉ (WAL) — `.phi-mission/JOURNAL/<agent>.jsonl`,
   append-only : chaque appel d'outil significatif y est journalé
   (appel → résultat). Comme le journal d'un système de fichiers :
   la mutation est écrite avant d'être considérée faite ; au crash,
   on rejoue le journal. Perte maximale : le dernier pas non journalé.

2. EMPREINTE RÉSIDUELLE — à l'exception, le mécanisme imprime
   mécaniquement `EMPREINTE_<agent>.json` (écriture atomique) : agent,
   item, dernier appel, dernier résultat, queue du carnet, erreur.
   C'est l'empreinte en pleine défaillance — produite par le
   mécanisme, pas par l'agent mourant.

3. `proteger()` — context manager : l'interception d'exceptions comme
   mécanisme interne. Enrobe le travail d'un agent ; à l'exception,
   écrit l'empreinte résiduelle puis re-lève. Usage :
       with journal.proteger(terrain, "a1", item="A5"):
           ...travail...

4. DEVOIR DES GARDIENS — les gardiens ne produisent pas les
   empreintes (ils sont faillibles comme les agents) : leur devoir
   est de VÉRIFIER qu'elles existent et sont lisibles
   (`verifier_empreintes()`). L'empreinte est mécanique ; l'audit
   est le devoir.

Limites honnêtes (les mêmes que le carnet, par construction) :
- le journal ne capte que l'extériorisé (appels, résultats) ;
- la pensée non extériorisée ne transite jamais par la VM ;
- la version pleinement mécanique vit dans le wrapper de boucle
  du runtime ; ici, c'est la forme appelable (convention +
  `proteger()`) que les agents et wrappers emploient.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from .sentinelle import DIR_ETAT, _ecrire_atomique, toucher

#: Sous-dossier du journal au pied de .phi-mission.
DIR_JOURNAL = "JOURNAL"

#: Vocabulaire contrôlé des événements du journal d'activité.
TYPES_JOURNAL = ("appel", "resultat", "empreinte", "note")


# ────────────────────────────────────────────────────────
# OUTILS BAS NIVEAU
# ────────────────────────────────────────────────────────

def _maintenant_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dir_journal(terrain: str) -> str:
    return os.path.join(os.path.abspath(terrain), DIR_ETAT, DIR_JOURNAL)


def _chemin_agent(terrain: str, agent: str) -> str:
    nom = "".join(c for c in agent if c.isalnum() or c in "-_.") or "anonyme"
    return os.path.join(_dir_journal(terrain), nom + ".jsonl")


def _chemin_empreinte(terrain: str, agent: str) -> str:
    nom = "".join(c for c in agent if c.isalnum() or c in "-_.") or "anonyme"
    return os.path.join(_dir_journal(terrain), "EMPREINTE_" + nom + ".json")


# ────────────────────────────────────────────────────────
# 1. JOURNAL D'ACTIVITÉ (append-only)
# ────────────────────────────────────────────────────────

def journaliser(terrain: str, agent: str, type_evt: str, detail: dict,
                item: str = "") -> dict:
    """Ajoute un événement au journal d'activité de l'agent.

    `detail` : dict libre et COURT (résumés, pas de dumps — ex.
    {"outil": "exec", "cmd": "lake env lean ...", "exit": 0}).
    Lève ValueError si le type est hors vocabulaire.
    """
    if type_evt not in TYPES_JOURNAL:
        raise ValueError(
            "type %r hors vocabulaire ; attendu l'un de %s"
            % (type_evt, ", ".join(TYPES_JOURNAL)))
    if not isinstance(detail, dict):
        raise ValueError("le détail doit être un dict")
    entree = {
        "t": _maintenant_iso(),
        "a": agent,
        "item": item or "",
        "type": type_evt,
        "d": detail,
    }
    chemin = _chemin_agent(terrain, agent)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entree, ensure_ascii=False) + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    # Régulateur natif : extérioriser, c'est vivre — la pulsation suit.
    try:
        toucher(terrain)
    except Exception:
        pass
    return entree


def lire(terrain: str, agent: str = None, n: int = None,
         types: tuple = None) -> list:
    """Lit le journal d'activité (le plus récent en dernier).

    Lignes corrompues ignorées (reboot en pleine écriture).
    """
    dossier = _dir_journal(terrain)
    entrees = []
    if not os.path.isdir(dossier):
        return entrees
    if agent:
        fichiers = [_chemin_agent(terrain, agent)]
    else:
        fichiers = sorted(
            os.path.join(dossier, f) for f in os.listdir(dossier)
            if f.endswith(".jsonl"))
    for chemin in fichiers:
        try:
            with open(chemin, encoding="utf-8") as fh:
                for ligne in fh:
                    ligne = ligne.strip()
                    if not ligne:
                        continue
                    try:
                        e = json.loads(ligne)
                    except ValueError:
                        continue
                    if (isinstance(e, dict)
                            and e.get("type") in TYPES_JOURNAL):
                        entrees.append(e)
        except OSError:
            continue
    if types:
        voulus = set(types)
        entrees = [e for e in entrees if e.get("type") in voulus]
    entrees.sort(key=lambda e: (e.get("t", ""), e.get("a", "")))
    if n is not None and n >= 0:
        entrees = entrees[-n:] if n else []
    return entrees


def agents(terrain: str) -> list:
    """Agents ayant un journal d'activité."""
    dossier = _dir_journal(terrain)
    if not os.path.isdir(dossier):
        return []
    return sorted(
        f[:-6] for f in os.listdir(dossier)
        if f.endswith(".jsonl"))


# ────────────────────────────────────────────────────────
# 2. EMPREINTE RÉSIDUELLE (mécanique, en pleine défaillance)
# ────────────────────────────────────────────────────────

def empreinte_residuelle(terrain: str, agent: str, item: str = "",
                         dernier_appel: dict = None,
                         dernier_resultat: dict = None,
                         erreur: str = "",
                         note: str = "") -> dict:
    """Imprime mécaniquement l'empreinte résiduelle d'un agent en
    défaillance : `EMPREINTE_<agent>.json` (écriture atomique) +
    entrée `empreinte` au journal.

    C'est le mécanisme qui imprime, pas l'agent mourant : à appeler
    dans le except (ou via `proteger()`). Ne lève jamais : une
    empreinte qui échoue à s'imprimer ne doit pas masquer l'erreur
    d'origine — l'échec est retourné dans le dict ("imprimee": False).
    """
    empreinte = {
        "t": _maintenant_iso(),
        "a": agent,
        "item": item or "",
        "dernier_appel": dernier_appel or {},
        "dernier_resultat": dernier_resultat or {},
        "erreur": (erreur or "")[:2000],
        "note": (note or "")[:500],
        "imprimee": False,
    }
    try:
        _ecrire_atomique(_chemin_empreinte(terrain, agent),
                         {**empreinte, "imprimee": True})
        empreinte["imprimee"] = True
    except OSError:
        empreinte["imprimee"] = False
    try:
        journaliser(terrain, agent, "empreinte",
                    {"item": item or "",
                     "erreur": (erreur or "")[:500],
                     "imprimee": empreinte["imprimee"]},
                    item=item)
    except (OSError, ValueError):
        pass
    return empreinte


def lire_empreinte(terrain: str, agent: str):
    """Lit l'empreinte résiduelle d'un agent, ou None."""
    try:
        with open(_chemin_empreinte(terrain, agent), encoding="utf-8") as fh:
            e = json.load(fh)
    except (OSError, ValueError):
        return None
    return e if isinstance(e, dict) else None


def empreintes(terrain: str) -> list:
    """Toutes les empreintes résiduelles (agents en défaillance constatée)."""
    dossier = _dir_journal(terrain)
    trouvees = []
    if not os.path.isdir(dossier):
        return trouvees
    for f in sorted(os.listdir(dossier)):
        if f.startswith("EMPREINTE_") and f.endswith(".json"):
            e = lire_empreinte(terrain, f[len("EMPREINTE_"):-len(".json")])
            if e is not None:
                trouvees.append(e)
    return trouvees


# ────────────────────────────────────────────────────────
# 3. PROTEGER — l'interception d'exceptions comme mécanisme
# ────────────────────────────────────────────────────────

class proteger:
    """Context manager : enrobe le travail, imprime l'empreinte à
    l'exception puis re-lève.

        with journal.proteger(terrain, "a1", item="A5"):
            ...travail de l'agent...

    À l'exception : empreinte résiduelle mécanique (dernier appel
    connu si fourni via `noter_appel()`), puis l'exception remonte
    inchangée — le mécanisme imprime, il ne répare pas.
    """

    def __init__(self, terrain: str, agent: str, item: str = ""):
        self.terrain = terrain
        self.agent = agent
        self.item = item
        self.dernier_appel = {}
        self.dernier_resultat = {}

    def noter_appel(self, detail: dict):
        """Journalise un appel (et le retient comme 'dernier appel')."""
        self.dernier_appel = dict(detail)
        return journaliser(self.terrain, self.agent, "appel",
                           dict(detail), item=self.item)

    def noter_resultat(self, detail: dict):
        """Journalise un résultat (et le retient comme 'dernier résultat')."""
        self.dernier_resultat = dict(detail)
        return journaliser(self.terrain, self.agent, "resultat",
                           dict(detail), item=self.item)

    def __enter__(self):
        journaliser(self.terrain, self.agent, "note",
                    {"debut": True}, item=self.item)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            journaliser(self.terrain, self.agent, "note",
                        {"fin": "ok"}, item=self.item)
            return False
        empreinte_residuelle(
            self.terrain, self.agent, item=self.item,
            dernier_appel=self.dernier_appel,
            dernier_resultat=self.dernier_resultat,
            erreur="%s: %s" % (exc_type.__name__, exc_val),
            note="empreinte mécanique à l'exception (proteger)")
        return False  # re-lève : le mécanisme imprime, il ne répare pas


# ────────────────────────────────────────────────────────
# 4. DEVOIR DES GARDIENS : vérifier les empreintes
# ────────────────────────────────────────────────────────

def verifier_empreintes(terrain: str) -> dict:
    """Devoir des gardiens : VÉRIFIER les empreintes, pas les produire.

    Retourne {"agents_journalises": [...], "empreintes": [...],
    "orphelines_sans_journal": [...]} — les gardiens montrent l'état
    de la protection, ils ne la fabriquent pas.
    """
    liste_agents = agents(terrain)
    liste_empreintes = empreintes(terrain)
    agents_empreints = {e.get("a") for e in liste_empreintes}
    orphelines = sorted(a for a in agents_empreints
                        if a not in liste_agents)
    return {
        "agents_journalises": liste_agents,
        "empreintes": [
            {"a": e.get("a"), "t": e.get("t"), "item": e.get("item"),
             "erreur": (e.get("erreur") or "")[:200]}
            for e in liste_empreintes
        ],
        "orphelines_sans_journal": orphelines,
    }


def rendre_verification_console(verif: dict) -> str:
    """Rendu texte de la vérification (le gardien montre)."""
    lignes = ["Vérification des empreintes (devoir des gardiens) :",
              "  agents journalisés : %d" % len(verif["agents_journalises"]),
              "  empreintes résiduelles : %d" % len(verif["empreintes"])]
    for e in verif["empreintes"]:
        lignes.append("    - %s [%s] %s : %s"
                      % (e.get("a"), e.get("t"), e.get("item"),
                         e.get("erreur")))
    if verif["orphelines_sans_journal"]:
        lignes.append("  ⚠ empreintes sans journal : %s"
                      % ", ".join(verif["orphelines_sans_journal"]))
    return "\n".join(lignes)
