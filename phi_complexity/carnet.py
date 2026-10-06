"""
carnet.py — La boîte noire des raisonnements (2026-10-05).

Constat (Tomy) : un reboot tue les sessions d'agents, et avec elles les
raisonnements en cours qui n'ont jamais été extériorisés. La sentinelle
sauve l'ÉTAT des missions ; le carnet sauve leur MÉMOIRE de travail :
où chaque agent en était, ce qu'il avait conclu, ce qu'il allait faire.

Limite honnête, posée d'abord : le raisonnement interne d'un agent vit
côté modèle, pas sur la VM. Ce qu'un agent n'a jamais extériorisé —
dans un fichier, un appel d'outil, une note — aucun mécanisme ne peut
le récupérer. Le miroir AUTOMATIQUE de tous les appels d'outils
demanderait le runtime lui-même ; depuis Phi-Complexity seul, c'est
impossible. Le carnet repose donc sur une discipline : EXTÉRIORISER
SOUVENT. L'unité récupérable, c'est « tout ce que l'agent a écrit » ;
la règle, c'est « écrire avant de penser trop loin ».

Formats (ordre de Tomy) : JSON et .md, fichiers les plus petits
possibles, liberté totale de contexte et d'organisation :
- JSONL append-only (une ligne = un événement) : on n'écrit qu'en
  ajout, jamais de réécriture du fichier — coût d'écriture minimal,
  pas de JSON tronqué par un reboot au milieu d'une réécriture.
- Clés courtes (t, a, item, type, msg, tags, x), messages tronqués
  (500 caractères), résumés plutôt que dumps, pointeurs vers des
  fichiers plutôt que contenus inline.
- Le .md n'est jamais stocké en double : c'est un RENDU généré à la
  demande depuis le JSONL (source unique). `phi carnet rendre`.
- Un flux par agent (`.phi-mission/CARNET/<agent>.jsonl`) : les agents
  écrivent en parallèle sans conflit ; l'append POSIX est atomique
  pour les petites écritures, et les lignes corrompues par un reboot
  en pleine écriture sont ignorées à la lecture (la dernière ligne
  peut mourir, jamais le carnet).

Vocabulaire contrôlé des événements (l'enveloppe est imposée, le
contenu est libre) :
    note     — checkpoint de raisonnement (où j'en suis / conclu / suite)
    acte     — miroir d'une action significative (outil, résumé, statut)
    decision — décision prise et son motif
    erreur   — erreur rencontrée et ce qu'elle exige
    trouvee  — découverte (résultat, mesure, réfutation)

Garde constitutionnelle : le carnet MONTRE la mémoire de travail ; il
ne rejoue jamais rien tout seul. La reprise reste une décision humaine.
"""

import json
import os
from datetime import datetime, timezone

from .sentinelle import DIR_ETAT, toucher

#: Sous-dossier des carnets au pied de .phi-mission.
DIR_CARNET = "CARNET"

#: Vocabulaire contrôlé des événements.
TYPES_EVENEMENT = ("note", "acte", "decision", "erreur", "trouvee")

#: Longueur maximale d'un message (tronqué au-delà, avec marqueur).
MSG_MAX = 500

#: Nombre d'entrées gardées par défaut par `archiver`.
GARDER_DEFAUT = 200


# ────────────────────────────────────────────────────────
# OUTILS BAS NIVEAU
# ────────────────────────────────────────────────────────

def _maintenant_iso() -> str:
    """Horodatage UTC ISO-8601 (sans microsecondes, lisible)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dir_carnet(terrain: str) -> str:
    return os.path.join(os.path.abspath(terrain), DIR_ETAT, DIR_CARNET)


def _chemin_agent(terrain: str, agent: str) -> str:
    nom = "".join(c for c in agent if c.isalnum() or c in "-_.") or "anonyme"
    return os.path.join(_dir_carnet(terrain), nom + ".jsonl")


def _normaliser_entree(brute: dict):
    """Valide une entrée lue : enveloppe minimale, sinon None."""
    if not isinstance(brute, dict):
        return None
    if brute.get("type") not in TYPES_EVENEMENT:
        return None
    if not isinstance(brute.get("msg"), str):
        return None
    return brute


# ────────────────────────────────────────────────────────
# 1. ÉCRITURE (append-only)
# ────────────────────────────────────────────────────────

def noter(terrain: str, agent: str, type_evt: str, msg: str,
          item: str = "", tags=(), contexte=None) -> dict:
    """Ajoute un événement au carnet de l'agent (append-only).

    Lève ValueError si le type est hors vocabulaire. Le message est
    tronqué à MSG_MAX caractères (avec marqueur « …[tronqué] »).
    Retourne l'entrée écrite.
    """
    if type_evt not in TYPES_EVENEMENT:
        raise ValueError(
            "type %r hors vocabulaire ; attendu l'un de %s"
            % (type_evt, ", ".join(TYPES_EVENEMENT)))
    if not isinstance(msg, str) or not msg.strip():
        raise ValueError("le message ne peut pas être vide")
    texte = msg.strip()
    if len(texte) > MSG_MAX:
        texte = texte[:MSG_MAX] + "…[tronqué]"
    entree = {
        "t": _maintenant_iso(),
        "a": agent,
        "item": item or "",
        "type": type_evt,
        "msg": texte,
        "tags": [str(t) for t in (tags or [])][:12],
    }
    if contexte is not None:
        if not isinstance(contexte, dict):
            raise ValueError("le contexte libre doit être un dict")
        entree["x"] = contexte
    chemin = _chemin_agent(terrain, agent)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    # Append-only : jamais de réécriture, donc jamais de fichier
    # tronqué par un reboot — au pire la dernière ligne est incomplète
    # et sera ignorée à la lecture.
    with open(chemin, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entree, ensure_ascii=False) + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    # Régulateur natif : extérioriser, c'est vivre — la pulsation suit.
    # Best-effort : ne fait jamais échouer la note.
    try:
        toucher(terrain)
    except Exception:
        pass
    return entree


# ────────────────────────────────────────────────────────
# 2. LECTURE
# ────────────────────────────────────────────────────────

def lire(terrain: str, agent: str = None, n: int = None,
         types: tuple = None) -> list:
    """Lit les événements (le plus récent en dernier).

    agent=None : tous les agents, triés par horodatage. n : ne garder
    que les n derniers. types : filtrer sur le vocabulaire.
    Les lignes corrompues (reboot en pleine écriture) sont ignorées
    silencieusement — le carnet survit à sa dernière ligne.
    """
    dossiers = _dir_carnet(terrain)
    entrees = []
    if not os.path.isdir(dossiers):
        return entrees
    fichiers = []
    if agent:
        fichiers = [_chemin_agent(terrain, agent)]
    else:
        fichiers = sorted(
            os.path.join(dossiers, f) for f in os.listdir(dossiers)
            if f.endswith(".jsonl") and not f.endswith(".archive.jsonl"))
    for chemin in fichiers:
        try:
            with open(chemin, encoding="utf-8") as fh:
                for ligne in fh:
                    ligne = ligne.strip()
                    if not ligne:
                        continue
                    try:
                        brute = json.loads(ligne)
                    except ValueError:
                        continue  # ligne tronquée par un reboot : ignorée
                    entree = _normaliser_entree(brute)
                    if entree is not None:
                        entrees.append(entree)
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
    """Agents ayant un carnet (flux non vide ou pas)."""
    dossiers = _dir_carnet(terrain)
    if not os.path.isdir(dossiers):
        return []
    return sorted(
        f[:-6] for f in os.listdir(dossiers)
        if f.endswith(".jsonl") and not f.endswith(".archive.jsonl"))


def compter(terrain: str, agent: str = None) -> int:
    """Nombre d'événements valides (mesure, pas verdict)."""
    return len(lire(terrain, agent=agent))


# ────────────────────────────────────────────────────────
# 3. RENDU .md (généré, jamais stocké en double)
# ────────────────────────────────────────────────────────

def rendre_md(terrain: str, agent: str = None, n: int = 50) -> str:
    """Rend les événements en Markdown lisible (vue générée).

    Le .md est un rendu, pas une source : la source unique reste le
    JSONL. On génère à la demande pour ne jamais stocker deux fois.
    """
    entrees = lire(terrain, agent=agent, n=n)
    titre_agent = agent or "tous les agents"
    lignes = [
        "# Carnet — %s" % titre_agent,
        "",
        "_Généré le %s depuis les flux JSONL (source unique). "
        "%d événement(s)._" % (_maintenant_iso(), len(entrees)),
        "",
    ]
    courant = None
    for e in entrees:
        if e.get("a") != courant:
            courant = e.get("a")
            lignes.append("## Agent `%s`" % courant)
            lignes.append("")
        tags = " ".join("#%s" % t for t in e.get("tags", []))
        item = e.get("item") or "—"
        lignes.append("### [%s] %s — item `%s`"
                      % (e.get("type", "?"), e.get("t", "?"), item))
        lignes.append("")
        lignes.append(e.get("msg", ""))
        if tags:
            lignes.append("")
            lignes.append(tags)
        lignes.append("")
    return "\n".join(lignes).rstrip() + "\n"


# ────────────────────────────────────────────────────────
# 4. REPRISE (mémoire de travail pour le resumer)
# ────────────────────────────────────────────────────────

def resume_reprise(terrain: str, n_par_agent: int = 5) -> str:
    """Mémoire de travail compacte pour un brief de reprise.

    Les n derniers événements par agent : où chacun en était quand
    tout s'est arrêté. À joindre au brief sentinelle.
    """
    liste_agents = agents(terrain)
    if not liste_agents:
        return ("Carnet : aucun flux d'agent — aucune mémoire de travail "
                "extériorisée à reprendre.")
    lignes = ["Carnet — mémoire de travail (%d agent(s)) :" % len(liste_agents)]
    for nom in liste_agents:
        dernieres = lire(terrain, agent=nom, n=n_par_agent)
        lignes.append("  Agent `%s` (%d événement(s), %d dernières) :"
                      % (nom, compter(terrain, agent=nom), len(dernieres)))
        if not dernieres:
            lignes.append("    (carnet vide)")
        for e in dernieres:
            item = " [item %s]" % e["item"] if e.get("item") else ""
            lignes.append("    - [%s]%s %s : %s"
                          % (e.get("type", "?"), item,
                             e.get("t", "?"), e.get("msg", "")))
    lignes.append("Le carnet montre où chacun en était ; il ne rejoue rien.")
    return "\n".join(lignes)


# ────────────────────────────────────────────────────────
# 5. ARCHIVAGE (garder les fichiers petits)
# ────────────────────────────────────────────────────────

def archiver(terrain: str, agent: str,
             garder: int = GARDER_DEFAUT) -> dict:
    """Archive les vieilles entrées d'un agent, garde les `garder`
    dernières dans le flux actif. L'archive est datée, en JSONL
    (lisible, grep-able) : `.phi-mission/CARNET/<agent>.archive-
    <AAAAMMJJ-HHMMSS>.jsonl`.

    Retourne {"archive": chemin ou None, "gardees": n, "archivees": m}.
    """
    if garder < 0:
        raise ValueError("garder doit être ≥ 0")
    chemin = _chemin_agent(terrain, agent)
    if not os.path.isfile(chemin):
        return {"archive": None, "gardees": 0, "archivees": 0}
    with open(chemin, encoding="utf-8") as fh:
        lignes = [l for l in fh if l.strip()]
    if len(lignes) <= garder:
        return {"archive": None, "gardees": len(lignes), "archivees": 0}
    a_archiver, a_garder = lignes[:-garder], lignes[-garder:]
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    nom_archive = "%s.archive-%s.jsonl" % (
        os.path.basename(chemin)[:-6], horodatage)
    chemin_archive = os.path.join(_dir_carnet(terrain), nom_archive)
    with open(chemin_archive, "w", encoding="utf-8") as fh:
        fh.writelines(a_archiver)
    # Réécriture atomique du flux actif (le seul endroit où le carnet
    # réécrit au lieu d'ajouter — via os.replace, jamais de tronqué).
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(a_garder)
    os.replace(tmp, chemin)
    return {"archive": chemin_archive, "gardees": len(a_garder),
            "archivees": len(a_archiver)}
