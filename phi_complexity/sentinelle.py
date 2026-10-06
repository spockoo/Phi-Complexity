"""
sentinelle.py — Survie des missions au reboot (2026-10-05).

Constat (Tomy) : la VM peut redémarrer à tout moment. Un reboot tue les
processus — compilations Lean en vol, sessions d'agents, boucles de
surveillance — mais jamais les fichiers sous ~. Une mission dont l'état
ne vit que dans des processus est donc mortelle ; une mission dont
l'état vit sur disque est reprenable.

Ce module fait quatre choses, toutes sur disque durable :
1. ÉTAT STRUCTURÉ — `.phi-mission/MISSION_STATE.json` : la mission
   écrite en lisible-machine (items de travail à statuts typés),
   pas seulement en prose de journal.
2. PULSATION — `.phi-mission/PULSE.json` : battement de cœur horodaté
   de l'agent en charge. Une pulsation expirée ne prouve pas la mort,
   elle la *suggère* (mesure, pas verdict).
3. REPRISE — `check` montre les missions orphelines, `reprise`
   génère le brief de reprise (état, items à relancer, vérifications
   suggérées).
4. ENCHAÎNEMENT — `enchaine` fait avancer une mission VRAIMENT figée
   vers sa suite naturelle : vérifie qu'aucun processus n'est vivant
   (garde anti-faux-positif), marque les items figés comme A-REPRENDRE,
   reprend la pulsation, journalise. L'état est chaîné, prêt pour le
   coordinateur/humain qui prend le relais. Ne spawne jamais d'agent
   ni ne relance de compilation — ça reste une décision.

Doctrine (ordre Tomy 2026-10-05) : quand un développement est figé et
vérifié mort, la sentinelle ne se contente plus de le signaler — elle
l'enchaîne vers sa suite naturelle. Le témoin montre ET fait avancer
l'état ; il ne décide jamais du contenu du travail.

Statuts typés des items (vocabulaire contrôlé) :
    EN-COURS, TERMINE, INTERROMPU, A-REPRENDRE, BLOQUE
"""

import json
import os
import tempfile
from datetime import datetime, timezone

#: Dossier d'état au pied du terrain de mission (durable : sous ~).
DIR_ETAT = ".phi-mission"

#: Fichiers d'état.
FICHIER_ETAT = "MISSION_STATE.json"
FICHIER_PULSE = "PULSE.json"

#: Vocabulaire contrôlé des items de travail.
STATUTS_ITEM = ("EN-COURS", "TERMINE", "INTERROMPU", "A-REPRENDRE", "BLOQUE")

#: Items considérés « à relancer » dans un brief de reprise.
STATUTS_A_RELANCER = ("EN-COURS", "INTERROMPU", "A-REPRENDRE")

#: Seuil par défaut : pulsation plus vieille = mission présumée orpheline.
SEUIL_ORPHELINE_S = 1800  # 30 minutes


# ────────────────────────────────────────────────────────
# OUTILS BAS NIVEAU
# ────────────────────────────────────────────────────────

def _maintenant_iso() -> str:
    """Horodatage UTC ISO-8601 (sans microsecondes, lisible)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _age_s(iso: str):
    """Âge en secondes d'un horodatage ISO, ou None si illisible."""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError):
        return None


def _dir_etat(terrain: str) -> str:
    return os.path.join(os.path.abspath(terrain), DIR_ETAT)


def _ecrire_atomique(chemin: str, data: dict) -> None:
    """Écriture atomique : fichier temporaire + os.replace.

    Un reboot entre les deux ne laisse jamais un JSON tronqué à la
    place de l'ancien — soit l'ancien, soit le nouveau.
    """
    dossier = os.path.dirname(chemin)
    os.makedirs(dossier, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dossier, prefix=".tmp-",
                               suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lire_json(chemin: str):
    try:
        with open(chemin, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ────────────────────────────────────────────────────────
# 1. ÉTAT STRUCTURÉ
# ────────────────────────────────────────────────────────

def init(terrain: str, nom: str) -> dict:
    """Initialise l'état d'une mission. Idempotent : ne écrase jamais.

    Retourne {"cree": True} à la création, {"cree": False} si l'état
    existait déjà (l'existant est préservé tel quel).
    """
    chemin = os.path.join(_dir_etat(terrain), FICHIER_ETAT)
    existant = _lire_json(chemin)
    if isinstance(existant, dict) and existant.get("nom"):
        return {"cree": False, "nom": existant.get("nom")}
    etat = {
        "nom": nom,
        "terrain": os.path.abspath(terrain),
        "cree_iso": _maintenant_iso(),
        "maj_iso": _maintenant_iso(),
        "items": {},
    }
    _ecrire_atomique(chemin, etat)
    return {"cree": True, "nom": nom}


def lire_etat(terrain: str):
    """Lit l'état structuré, ou None si absent/illisible."""
    data = _lire_json(os.path.join(_dir_etat(terrain), FICHIER_ETAT))
    return data if isinstance(data, dict) else None


def checkpoint(terrain: str, item: str, statut: str, note: str = "") -> dict:
    """Enregistre le statut d'un item de travail (vocabulaire contrôlé).

    Lève ValueError si le statut est hors vocabulaire ou si la mission
    n'est pas initialisée (init d'abord).
    """
    if statut not in STATUTS_ITEM:
        raise ValueError(
            "statut %r hors vocabulaire ; attendu l'un de %s"
            % (statut, ", ".join(STATUTS_ITEM)))
    etat = lire_etat(terrain)
    if etat is None:
        raise ValueError("mission non initialisée : 'phi sentinelle init' d'abord")
    etat.setdefault("items", {})[item] = {
        "statut": statut,
        "note": note,
        "maj_iso": _maintenant_iso(),
    }
    etat["maj_iso"] = _maintenant_iso()
    _ecrire_atomique(os.path.join(_dir_etat(terrain), FICHIER_ETAT), etat)
    toucher(terrain)  # régulateur : un checkpoint est un signe de vie
    return etat["items"][item]


# ────────────────────────────────────────────────────────
# 2. PULSATION
# ────────────────────────────────────────────────────────

def pulse(terrain: str, agent: str = "") -> dict:
    """Écrit le battement de cœur horodaté de l'agent en charge."""
    battement = {
        "pulse_iso": _maintenant_iso(),
        "agent": agent,
        "terrain": os.path.abspath(terrain),
    }
    _ecrire_atomique(os.path.join(_dir_etat(terrain), FICHIER_PULSE),
                     battement)
    return battement


def lire_pulse(terrain: str):
    """Lit la dernière pulsation, ou None si absente/illisible."""
    data = _lire_json(os.path.join(_dir_etat(terrain), FICHIER_PULSE))
    return data if isinstance(data, dict) else None


def toucher(terrain: str) -> bool:
    """Régulateur de pulsation natif : rafraîchit la pulsation SANS
    exiger un geste volontaire de l'agent.

    Appelé automatiquement à chaque extériorisation (carnet.noter,
    journal.journaliser, sentinelle.checkpoint) : le cœur n'oublie
    plus de battre — il bat chaque fois que la mission s'exprime.
    L'agent propriétaire est conservé (seul l'horodatage avance) ;
    si aucune pulsation n'existe encore, le régulateur signe de son
    propre nom, en toute transparence.

    Ne crée jamais l'état d'une mission non initialisée (retourne
    False). Best-effort : n'échoue jamais l'écriture qui l'appelle.
    """
    try:
        d = _dir_etat(terrain)
        if not os.path.isfile(os.path.join(d, FICHIER_ETAT)):
            return False
        precedent = lire_pulse(terrain) or {}
        _ecrire_atomique(os.path.join(d, FICHIER_PULSE), {
            "pulse_iso": _maintenant_iso(),
            "agent": precedent.get("agent") or "regulateur-auto",
            "terrain": os.path.abspath(terrain),
        })
        return True
    except OSError:
        return False


def age_pulse_s(terrain: str):
    """Âge de la dernière pulsation en secondes, ou None."""
    p = lire_pulse(terrain)
    if not p:
        return None
    return _age_s(p.get("pulse_iso", ""))


def mission_cloturee(terrain: str) -> bool:
    """True si la mission est clôturée : au moins un item suivi et tous
    les items au statut TERMINE.

    Une mission clôturée n'est pas orpheline : personne ne pulse après
    une clôture, et c'est normal. Sans cette distinction, chaque ronde
    crie à l'orpheline sur les missions finies (faux positifs).
    """
    etat = lire_etat(terrain) or {}
    items = etat.get("items") or {}
    if isinstance(items, list):
        statuts = [(it.get("statut") or "").upper() for it in items]
    else:
        statuts = [(v.get("statut") or "").upper() for v in items.values()]
    return bool(statuts) and all(s == "TERMINE" for s in statuts)


def est_orpheline(terrain: str, seuil_s: int = SEUIL_ORPHELINE_S) -> bool:
    """Mesure : True si la mission semble orpheline.

    Orpheline = pas de pulsation, pulsation illisible, ou pulsation
    plus vieille que le seuil — SAUF si la mission est clôturée
    (tous items TERMINE) : une mission finie ne pulse plus, ce n'est
    pas une mort. C'est une présomption affichée, jamais une décision
    d'en prendre le deuil.
    """
    if mission_cloturee(terrain):
        return False
    age = age_pulse_s(terrain)
    if age is None:
        return True
    return age > seuil_s


def orphelines(racine_missions: str,
               seuil_s: int = SEUIL_ORPHELINE_S) -> list:
    """Balaye un dossier de missions, retourne les présumées orphelines.

    Chaque entrée : {"terrain", "nom", "age_pulse_s", "maj_etat_iso"}.
    Lecture seule ; ne touche à rien.
    """
    trouvees = []
    racine = os.path.abspath(racine_missions)
    if not os.path.isdir(racine):
        return trouvees
    for nom in sorted(os.listdir(racine)):
        terrain = os.path.join(racine, nom)
        if not os.path.isdir(os.path.join(terrain, DIR_ETAT)):
            continue
        if not est_orpheline(terrain, seuil_s):
            continue
        etat = lire_etat(terrain) or {}
        trouvees.append({
            "terrain": terrain,
            "nom": etat.get("nom", nom),
            "age_pulse_s": age_pulse_s(terrain),
            "maj_etat_iso": etat.get("maj_iso", "?"),
        })
    return trouvees


def rendre_orphelines_console(trouvees: list) -> str:
    """Rendu texte de la liste des orphelines (le témoin montre)."""
    if not trouvees:
        return "Aucune mission orpheline détectée."
    lignes = ["Missions présumées orphelines (pulsation expirée) :"]
    for t in trouvees:
        age = t["age_pulse_s"]
        age_txt = ("jamais" if age is None
                   else "%dh%02dm" % (int(age // 3600), int(age % 3600) // 60))
        lignes.append("  - %s (pulsation : %s, état : %s)"
                      % (t["nom"], age_txt, t["maj_etat_iso"]))
    lignes.append("Vérifier avant toute reprise : ces missions sont "
                  "présumées, pas déclarées mortes.")
    return "\n".join(lignes)


# ────────────────────────────────────────────────────────
# 4. DORMANTS — détecter, brief­er, confier le réveil
# ────────────────────────────────────────────────────────

#: Items considérés « coincés » pour la détection de dormance.
STATUTS_COINCES = ("EN-COURS", "INTERROMPU", "A-REPRENDRE", "BLOQUE")


def _dernieres_notes_carnet(terrain: str, item: str, n: int = 3) -> list:
    """Dernières notes carnet rattachées à un item (preuves de dormance)."""
    try:
        from . import carnet as mod_carnet
    except Exception:
        return []
    notes = []
    try:
        for nom in mod_carnet.agents(terrain):
            for e in mod_carnet.lire(terrain, agent=nom, n=50):
                if e.get("item") == item:
                    notes.append(e)
    except Exception:
        return []
    notes.sort(key=lambda e: e.get("t", ""))
    return notes[-n:] if n else []


def _appels_sans_reponse(terrain: str, item: str) -> int:
    """Appels journalés sans résultat (en attente peut-être morte)."""
    try:
        from . import journal as mod_journal
    except Exception:
        return 0
    try:
        entrees = mod_journal.lire(terrain, n=500)
    except Exception:
        return 0
    appels = sum(1 for e in entrees
                 if e.get("item") == item and e.get("type") == "appel")
    resultats = sum(1 for e in entrees
                    if e.get("item") == item and e.get("type") == "resultat")
    return max(0, appels - resultats)


def dormants(terrain: str, seuil_s: int = SEUIL_ORPHELINE_S) -> list:
    """Détecte les items présumés dormants, avec leurs preuves.

    Croise : items sentinelle coincés et anciens, dernières notes
    carnet (l'agent attendait quoi ?), appels journal sans résultat,
    âge de la pulsation. Chaque entrée :
    {"item", "statut", "age_s", "agents", "derniere_note",
     "appels_sans_reponse", "action_suggeree"}.
    Mesure, pas verdict : c'est au coordinateur (ou à l'humain) de
    réveiller — l'instrument ne touche à aucun agent.
    """
    etat = lire_etat(terrain)
    if etat is None:
        return []
    maintenant = datetime.now(timezone.utc)
    trouves = []
    for nom, entree in (etat.get("items", {}) or {}).items():
        if not isinstance(entree, dict):
            continue
        if entree.get("statut") not in STATUTS_COINCES:
            continue
        age = _age_s(entree.get("maj_iso", ""))
        if age is None or age <= seuil_s:
            continue
        notes = _dernieres_notes_carnet(terrain, nom)
        agents_notes = sorted({e.get("a", "?") for e in notes})
        derniere = notes[-1] if notes else {}
        sans_reponse = _appels_sans_reponse(terrain, nom)
        statut = entree.get("statut")
        if statut == "BLOQUE":
            action = ("débloquer : lever le blocage nommé (« %s »), "
                      "puis reprendre l'item" % entree.get("note", ""))
        else:
            action = ("réveiller : respawner un successeur avec le brief "
                      "d'héritage (carnet + snapshots + journal), ou "
                      "reprendre l'agent s'il est joignable")
        trouves.append({
            "item": nom,
            "statut": statut,
            "age_s": age,
            "agents": agents_notes,
            "derniere_note": derniere.get("msg", ""),
            "derniere_note_t": derniere.get("t", ""),
            "appels_sans_reponse": sans_reponse,
            "action_suggeree": action,
        })
    trouves.sort(key=lambda d: d["age_s"], reverse=True)
    return trouves


def rendre_dormants_console(trouves: list) -> str:
    """Rendu texte de la liste des dormants (le témoin montre)."""
    if not trouves:
        return "Aucun item dormant détecté."
    lignes = ["Items présumés dormants (coincés et sans activité) :"]
    for d in trouves:
        age = d["age_s"]
        age_txt = "%dh%02dm" % (int(age // 3600), int(age % 3600) // 60)
        agents = ", ".join(d["agents"]) if d["agents"] else "aucun flux"
        lignes.append("  - [%s] %s (coincé depuis %s, agents : %s)"
                      % (d["statut"], d["item"], age_txt, agents))
        if d["derniere_note"]:
            lignes.append("      dernière note (%s) : %s"
                          % (d["derniere_note_t"], d["derniere_note"][:120]))
        if d["appels_sans_reponse"]:
            lignes.append("      ⚠ %d appel(s) journalé(s) sans résultat"
                          % d["appels_sans_reponse"])
    lignes.append("Vérifier avant tout réveil : ce sont des présomptions, "
                  "pas des constats de mort.")
    return "\n".join(lignes)


def brief_reveil(terrain: str, seuil_s: int = SEUIL_ORPHELINE_S) -> str:
    """Brief de réveil : la liste des dormants + leurs briefs d'héritage,
    à confier au coordinateur pour un déblocage en une passe.

    L'instrument DÉTECTE et BRIEFE ; il ne réveille rien seul — le
    réveil (reprendre l'agent ou respawner un successeur) reste une
    décision du coordinateur ou de l'humain.
    """
    etat = lire_etat(terrain)
    if etat is None:
        return ("Aucun état structuré — détection des dormants impossible "
                "(mission jamais initialisée ?).")
    trouves = dormants(terrain, seuil_s=seuil_s)
    lignes = [
        "BRIEF DE RÉVEIL — mission « %s »" % etat.get("nom", "?"),
        "Terrain : %s" % etat.get("terrain", "?"),
        "",
        rendre_dormants_console(trouves),
        "",
    ]
    if trouves:
        lignes.append("Héritage par item (à verser au successeur) :")
        try:
            from . import carnet as mod_carnet
            for d in trouves:
                lignes.append("  Item `%s` :" % d["item"])
                notes = _dernieres_notes_carnet(terrain, d["item"], n=5)
                if notes:
                    for e in notes:
                        lignes.append("    - [%s] %s <%s> : %s"
                                      % (e.get("type", "?"), e.get("t", "?"),
                                         e.get("a", "?"), e.get("msg", "")))
                else:
                    lignes.append("    (aucune note carnet — héritage pauvre : "
                                  "relire le journal du coordinateur)")
        except Exception:
            pass
        lignes.append("")
        try:
            from . import snapshot as mod_snapshot
            entrees = mod_snapshot.lister(terrain)
            if entrees:
                lignes.append("Snapshots disponibles pour restauration : %d "
                              "(`phi filet liste --terrain <terrain>`)."
                              % len(entrees))
                lignes.append("")
        except Exception:
            pass
        lignes += [
            "Mode d'emploi (au coordinateur) :",
            "  1. Pour chaque item, décider : reprendre l'agent s'il est",
            "     joignable, sinon respawner un successeur avec l'héritage",
            "     ci-dessus.",
            "  2. Un créneau de compilation à la fois ; ne jamais réveiller",
            "     deux agents sur le même item.",
            "  3. À la reprise : `phi sentinelle checkpoint --item <item>",
            "     --statut EN-COURS` + pulsation.",
            "",
        ]
    lignes.append("Ce brief détecte et briefe ; il ne réveille rien seul.")
    return "\n".join(lignes)


# ────────────────────────────────────────────────────────
# 3. BRIEF DE REPRISE
# ────────────────────────────────────────────────────────

def items_a_relancer(terrain: str) -> list:
    """Items dont le statut appelle une relance (mesure, pas verdict)."""
    etat = lire_etat(terrain) or {}
    items = etat.get("items", {}) or {}
    return sorted(
        (nom for nom, entree in items.items()
         if isinstance(entree, dict)
         and entree.get("statut") in STATUTS_A_RELANCER))


def brief_reprise(terrain: str) -> str:
    """Génère le brief de reprise d'une mission (texte, à confier à
    l'agent resumer). Ne relance rien : il décrit ce qu'il y a à
    vérifier et à relancer, dans l'ordre.
    """
    etat = lire_etat(terrain)
    if etat is None:
        return ("Aucun état structuré pour %s — reprise impossible par "
                "la sentinelle (mission jamais initialisée ?)."
                % os.path.abspath(terrain))
    nom = etat.get("nom", "?")
    age = age_pulse_s(terrain)
    age_txt = "jamais" if age is None else "il y a %d min" % int(age // 60)
    a_relancer = items_a_relancer(terrain)
    items = etat.get("items", {}) or {}

    lignes = [
        "BRIEF DE REPRISE — mission « %s »" % nom,
        "Terrain : %s" % etat.get("terrain", "?"),
        "Dernière pulsation : %s. Dernier état : %s."
        % (age_txt, etat.get("maj_iso", "?")),
        "",
        "1. Vérifier le dépôt : `git status --short`, `git log --oneline -3`,",
        "   comparer `git ls-files | wc -l` et `find <terrain> -type f | wc -l`.",
        "   Tout fichier non committé = travail à sauvegarder AVANT de relancer.",
        "2. Relire le journal de l'agent précédent (s'il existe) pour le contexte.",
        "3. Items à relancer (%d) :" % len(a_relancer),
    ]
    if a_relancer:
        for nom_item in a_relancer:
            entree = items.get(nom_item, {})
            lignes.append("   - [%s] %s — %s"
                          % (entree.get("statut", "?"), nom_item,
                             entree.get("note", "")))
    else:
        lignes.append("   (aucun — l'état ne signale rien en vol)")
    lignes += [
        "4. Relancer les compilations interrompues UNE À LA FOIS",
        "   (doctrine : un seul créneau de compilation).",
        "5. Reprendre la pulsation : `phi sentinelle pulse --terrain <terrain>`.",
        "",
    ]
    # Mémoire de travail du carnet (boîte noire) : où chacun en était.
    try:
        from . import carnet as mod_carnet
        lignes.append(mod_carnet.resume_reprise(terrain))
        lignes.append("")
    except Exception:
        pass
    # Filet snapshot : quoi restaurer ; empreintes : qui a échoué comment.
    try:
        from . import snapshot as mod_snapshot
        entrees = mod_snapshot.lister(terrain)
        if entrees:
            fichiers = sorted({e.get("f") for e in entrees})
            lignes.append("Snapshots disponibles : %d snapshot(s), %d fichier(s), "
                          "du %s au %s."
                          % (len(entrees), len(fichiers),
                             entrees[0].get("t", "?"), entrees[-1].get("t", "?")))
            lignes.append("  (voir `phi filet liste --terrain <terrain>`)")
            lignes.append("")
    except Exception:
        pass
    try:
        from . import journal as mod_journal
        verif = mod_journal.verifier_empreintes(terrain)
        if verif["empreintes"]:
            lignes.append("Empreintes résiduelles (%d) — agents en défaillance constatée :"
                          % len(verif["empreintes"]))
            for e in verif["empreintes"]:
                lignes.append("  - %s [%s] %s : %s"
                              % (e.get("a"), e.get("t"), e.get("item"),
                                 e.get("erreur")))
            lignes.append("")
    except Exception:
        pass
    lignes.append("Ce brief décrit ; il ne prouve pas que la reprise a eu lieu.")
    return "\n".join(lignes)


# ────────────────────────────────────────────────────────
# 5. ENCHAÎNEMENT — faire avancer les développements figés
# ────────────────────────────────────────────────────────

def _processus_vivants_terrain(terrain: str) -> list:
    """Processus vivants dont la ligne de commande mentionne le terrain.

    Garde anti-faux-positif : un coordinateur vivant mais silencieux
    (pas de pulsation récente) ne doit JAMAIS être enchaîné — l'état
    serait corrompu par une reprise concurrente. Mesure par `ps`,
    best-effort (liste vide si ps indisponible).

    Exclut le processus sentinelle lui-même (qui porte --terrain dans
    sa ligne de commande par construction).
    """
    import subprocess
    terrain_abs = os.path.abspath(terrain)
    vivants = []
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,args"], capture_output=True, text=True,
            timeout=10).stdout
    except Exception:
        return vivants
    for ligne in out.splitlines()[1:]:
        if terrain_abs in ligne and "ps -eo" not in ligne:
            # Ne pas se compter soi-même : la sentinelle porte --terrain.
            if "phi_complexity.cli sentinelle" in ligne:
                continue
            if "sentinelle enchaine" in ligne:
                continue
            parts = ligne.strip().split(None, 1)
            if parts:
                vivants.append({"pid": parts[0],
                                "cmd": (parts[1] if len(parts) > 1
                                        else "")[:120]})
    return vivants


def enchaine(terrain: str) -> dict:
    """Enchaîne une mission figée vers sa suite naturelle.

    Ordre Tomy 2026-10-05 : la sentinelle ne signale plus seulement —
    elle fait avancer l'état.

    Protocole :
    1. Si la mission n'est pas orpheline (pulsation fraîche) → rien à
       faire, retour {"action": "rien", "raison": "pulsation fraîche"}.
    2. Vérifier qu'aucun processus n'est vivant sur le terrain.
       Si vivant → FAUX POSITIF, ne rien toucher, le signaler.
    3. Si vraiment figée : marquer chaque item EN-COURS/INTERROMPU/
       A-REPRENDRE comme A-REPRENDRE (note d'enchaînement horodatée),
       reprendre la pulsation (agent="sentinelle-enchaine"), journaliser
       l'enchaînement dans le carnet.
    4. Retourner le rapport d'enchaînement.

    Ne spawne jamais d'agent, ne relance jamais de compilation : l'état
    est chaîné et prêt, la décision du contenu reste au coordinateur
    ou à l'humain qui prend le relais.
    """
    terrain_abs = os.path.abspath(terrain)
    resultat = {"terrain": terrain_abs, "action": "rien", "items": []}

    etat = lire_etat(terrain)
    if etat is None:
        resultat["raison"] = "mission jamais initialisée"
        return resultat

    # 1. Pulsation fraîche → rien à enchaîner.
    if not est_orpheline(terrain):
        resultat["raison"] = "pulsation fraîche, mission vivante"
        return resultat

    # 2. Garde anti-faux-positif : processus vivant ?
    vivants = _processus_vivants_terrain(terrain)
    if vivants:
        resultat["action"] = "faux-positif"
        resultat["raison"] = (
            "pulsation expirée mais %d processus vivant(s) sur le terrain "
            "— enchaînement refusé" % len(vivants))
        resultat["vivants"] = vivants
        return resultat

    # 3. Vraiment figée : enchaîner les items.
    items = etat.get("items", {}) or {}
    enchaines = []
    for nom, entree in items.items():
        if not isinstance(entree, dict):
            continue
        if entree.get("statut") in STATUTS_A_RELANCER:
            note = (entree.get("note", "") + " | enchaîné %s : suite "
                    "naturelle = reprise par le prochain coordinateur"
                    % _maintenant_iso()).strip(" |")
            etat["items"][nom] = {
                "statut": "A-REPRENDRE",
                "note": note,
                "maj_iso": _maintenant_iso(),
            }
            enchaines.append(nom)
    etat["maj_iso"] = _maintenant_iso()
    _ecrire_atomique(os.path.join(_dir_etat(terrain), FICHIER_ETAT), etat)

    # 4. Reprendre la pulsation (la sentinelle signe son acte).
    pulse(terrain, agent="sentinelle-enchaine")

    # 5. Journaliser dans le carnet (transparence totale).
    try:
        from . import carnet as mod_carnet
        mod_carnet.noter(
            terrain, agent="sentinelle-enchaine", item="",
            type="acte",
            msg=("ENCHAÎNEMENT automatique : mission vérifiée figée "
                 "(0 processus vivant), %d item(s) chaîné(s) vers "
                 "A-REPRENDRE : %s. Pulsation reprise. Prêt pour le "
                 "prochain coordinateur."
                 % (len(enchaines), ", ".join(enchaines) or "aucun")))
    except Exception:
        pass

    resultat["action"] = "enchaine"
    resultat["items"] = enchaines
    resultat["raison"] = ("%d item(s) figé(s) chaîné(s) vers A-REPRENDRE, "
                          "pulsation reprise" % len(enchaines))
    return resultat


def rendre_enchaine_console(resultat: dict) -> str:
    """Rendu texte du rapport d'enchaînement."""
    nom = os.path.basename(resultat.get("terrain", "?"))
    action = resultat.get("action", "rien")
    if action == "rien":
        return ("Mission « %s » : %s — rien enchaîné."
                % (nom, resultat.get("raison", "")))
    if action == "faux-positif":
        lignes = ["Mission « %s » : FAUX POSITIF — %s."
                  % (nom, resultat.get("raison", ""))]
        for v in resultat.get("vivants", []):
            lignes.append("  - PID %s : %s" % (v["pid"], v["cmd"]))
        lignes.append("Enchaînement refusé : l'état n'a pas été touché.")
        return "\n".join(lignes)
    lignes = ["Mission « %s » ENCHAÎNÉE : %s."
              % (nom, resultat.get("raison", ""))]
    for item in resultat.get("items", []):
        lignes.append("  - %s → A-REPRENDRE" % item)
    lignes.append("État chaîné et prêt pour le prochain coordinateur.")
    return "\n".join(lignes)
