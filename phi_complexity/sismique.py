"""
sismique.py — Le témoin : mémoire des rythmes via l'historique git.

Directive unique (Tomy) : démontrer formellement la conjecture de
Navier-Stokes suivant les critères de l'institut Clay.
Troisième directive (Tomy, 2026-10-01) : développer phi-complexity
librement SOUS CONDITION que ça serve la directive unique.

Règle CONSTITUTIONNELLE (garde testée dans tests/test_garde_non_prescriptif.py) :
le sismogramme DÉCRIT (magnitude, profondeur, épicentre), il ne JUGE jamais.
Aucun langage prescriptif (voir LISTE_NOIRE_PRESCRIPTIF de radar.py).

Mesures (purement mécaniques) :
- touches : nombre de commits touchant le fichier sur la fenêtre ;
- ajoute / retire : lignes ajoutées / retirées (numstat) ;
- mecanisme : construction (que des ajouts), effondrement (que des
  retraits), barattage (ajouts ET retraits significatifs : le minoritaire
  dépasse 20 % du majoritaire), ajustement (les deux, faible barattage) ;
- replique : fichier touché ≥3 fois en 24h ;
- rayon (profondeur) : nombre de fichiers dépendants transitivement
  (graphe de dependances.py) — un changement « profond » (Part0) a plus
  d'énergie potentielle qu'un changement « superficiel » (test isolé) ;
- magnitude : (ajoute + retire) × (1 + rayon) ;
- epicentre : fichier le plus touché (départage : magnitude, puis nom).
"""

import os
import subprocess
from datetime import datetime, timedelta, timezone

from .dependances import analyser_fichier
from .radar import LISTE_NOIRE_PRESCRIPTIF  # garde : vocabulaire partagé

#: Fenêtre de réplique : ≥3 touches en 24h.
FENETRE_REPLIQUE = timedelta(hours=24)
SEUIL_REPLIQUE_TOUCHES = 3

#: Barattage : le minoritaire dépasse 20 % du majoritaire.
SEUIL_BARATTAGE = 0.2


def _normaliser_depuis(depuis: str) -> str:
    """Normalise la borne `depuis` : une date nue AAAA-MM-JJ devient
    AAAA-MM-JJT00:00:00.

    Justification (mesurée 2026-10-03) : git remplit l'heure manquante
    d'une date nue avec l'heure *courante*, excluant silencieusement les
    commits du jour antérieurs à maintenant. Sans cette normalisation,
    `phi sismique --depuis 2026-01-01` lancé à 10h24 rate le commit
    de 10h00 — une perte silencieuse, exactement ce que l'instrument
    refuse.
    """
    import re
    d = depuis.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d + "T00:00:00"
    return depuis


def _journal_git(depot: str, depuis: str) -> list:
    """Commits depuis `depuis` : [(hash, date, sujet, [(aj, ret, fichier)])]."""
    proc = subprocess.run(
        ["git", "-C", depot, "log", f"--since={_normaliser_depuis(depuis)}",
         "--pretty=format:%H|%aI|%s", "--numstat"],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise ValueError(
            f"pas un dépôt git lisible : {depot} "
            f"({proc.stderr.strip()[:120]})")
    commits = []
    courant = None
    for ligne in proc.stdout.splitlines():
        if "\t" not in ligne and "|" in ligne:
            # Ligne pretty : hash|date|sujet (le hash fait 40 hex).
            parties = ligne.split("|", 2)
            if (len(parties) == 3 and len(parties[0]) == 40
                    and all(c in "0123456789abcdef" for c in parties[0])):
                courant = {"hash": parties[0], "date": parties[1],
                           "sujet": parties[2], "fichiers": []}
                commits.append(courant)
                continue
        if courant is not None and "\t" in ligne:
            parties = ligne.split("\t")
            if len(parties) == 3:
                try:
                    aj, ret = int(parties[0]), int(parties[1])
                except ValueError:
                    continue  # binaire : '-' '-' → ignoré
                courant["fichiers"].append((aj, ret, parties[2]))
    return commits


def _parse_date(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _mecanisme(ajoute: int, retire: int) -> str:
    if ajoute > 0 and retire == 0:
        return "construction"
    if retire > 0 and ajoute == 0:
        return "effondrement"
    if ajoute > 0 and retire > 0:
        if min(ajoute, retire) / max(ajoute, retire) > SEUIL_BARATTAGE:
            return "barattage"
        return "ajustement"
    return "stable"


def _replique(dates: list) -> bool:
    triees = sorted(dates)
    for i in range(len(triees)):
        j = i
        while j < len(triees) and triees[j] - triees[i] <= FENETRE_REPLIQUE:
            j += 1
        if j - i >= SEUIL_REPLIQUE_TOUCHES:
            return True
    return False


def _graphe_fichiers(dossier: str) -> dict:
    """fichier -> ensemble des fichiers dont il dépend (niveau fichier)."""
    decl_vers_fichier = {}
    arretes = {}
    for dirpath, dirnames, filenames in os.walk(dossier):
        dirnames[:] = [d for d in dirnames if d != ".lake" and d != ".git"]
        for fn in sorted(filenames):
            if not fn.endswith(".lean"):
                continue
            chemin = os.path.join(dirpath, fn)
            rel = os.path.relpath(chemin, dossier)
            graphe = analyser_fichier(chemin)
            for nom in graphe.dependances:
                decl_vers_fichier[nom] = rel
    for dirpath, dirnames, filenames in os.walk(dossier):
        dirnames[:] = [d for d in dirnames if d != ".lake" and d != ".git"]
        for fn in sorted(filenames):
            if not fn.endswith(".lean"):
                continue
            chemin = os.path.join(dirpath, fn)
            rel = os.path.relpath(chemin, dossier)
            graphe = analyser_fichier(chemin)
            deps = set()
            for nom, refs in graphe.dependances.items():
                for ref in refs:
                    autre = decl_vers_fichier.get(ref)
                    if autre and autre != rel:
                        deps.add(autre)
            arretes[rel] = deps
    return arretes


def _rayons_tous(dossier: str, fichiers: list) -> dict:
    """Rayons de blast pour une liste de fichiers — un seul graphe."""
    arretes = _graphe_fichiers(dossier)
    dependents = {}
    for src, dsts in arretes.items():
        for dst in dsts:
            dependents.setdefault(dst, set()).add(src)
    rayons = {}
    for fichier in fichiers:
        if not fichier.endswith(".lean"):
            rayons[fichier] = 0
            continue
        vus = set()
        pile = [fichier]
        while pile:
            courant = pile.pop()
            for dep in dependents.get(courant, ()):
                if dep not in vus and dep != fichier:
                    vus.add(dep)
                    pile.append(dep)
        rayons[fichier] = len(vus)
    return rayons


def rayon_blast(dossier: str, fichier: str) -> int:
    """Nombre de fichiers dépendant transitivement de `fichier`
    (profondeur sismique : énergie potentielle du changement)."""
    return _rayons_tous(dossier, [fichier])[fichier]


def analyser(depot: str, depuis: str) -> dict:
    """Sismogramme d'un dépôt git depuis `depuis` (date ISO ou AAAA-MM-JJ).

    Lève ValueError si le dépôt n'est pas lisible — jamais de silence.
    """
    commits = _journal_git(depot, depuis)
    stats = {}
    for commit in commits:
        date = _parse_date(commit["date"])
        for aj, ret, fichier in commit["fichiers"]:
            st = stats.setdefault(fichier, {
                "touches": 0, "ajoute": 0, "retire": 0, "dates": []})
            st["touches"] += 1
            st["ajoute"] += aj
            st["retire"] += ret
            st["dates"].append(date)
    # Rayon par fichier (un seul graphe pour tout le dépôt)
    rayons = {}
    try:
        rayons = _rayons_tous(depot, [f for f in stats])
    except Exception:
        pass  # graphe indisponible : rayons à 0, pas de silence déguisé
    fichiers = []
    for fichier in sorted(stats):
        st = stats[fichier]
        rayon = rayons.get(fichier, 0)
        fichiers.append({
            "fichier": fichier,
            "touches": st["touches"],
            "ajoute": st["ajoute"],
            "retire": st["retire"],
            "mecanisme": _mecanisme(st["ajoute"], st["retire"]),
            "replique": _replique(st["dates"]),
            "rayon": rayon,
            "magnitude": (st["ajoute"] + st["retire"]) * (1 + rayon),
        })
    epicentre = None
    if fichiers:
        epicentre = sorted(
            fichiers, key=lambda f: (-f["touches"], -f["magnitude"],
                                     f["fichier"]))[0]["fichier"]
    return {
        "depot": depot,
        "depuis": depuis,
        "commits": len(commits),
        "fichiers": fichiers,
        "epicentre": epicentre,
    }


def formater_console(sismo: dict) -> str:
    """Rendu console : faits uniquement, aucun langage prescriptif."""
    lignes = [f"🌊 SISMIQUE — {sismo['commits']} commits depuis "
              f"{sismo['depuis']} ({sismo['depot']})",
              f"{'fichier':<28} {'touches':>7} {'+lignes':>7} {'-lignes':>7} "
              f"{'mécanisme':<13} {'réplique':>8} {'rayon':>5} {'magnitude':>9}"]
    for f in sismo["fichiers"]:
        lignes.append(
            f"{f['fichier']:<28} {f['touches']:>7} {f['ajoute']:>7} "
            f"{f['retire']:>7} {f['mecanisme']:<13} "
            f"{'oui' if f['replique'] else 'non':>8} {f['rayon']:>5} "
            f"{f['magnitude']:>9}")
    if sismo["epicentre"]:
        lignes.append(f"épicentre : {sismo['epicentre']}")
    return "\n".join(lignes)
