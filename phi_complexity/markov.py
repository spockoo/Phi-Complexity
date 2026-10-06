"""
markov.py — Chaînes de Markov pour la chaîne des goulots (2026-10-05).

Idée (Tomy) : modéliser chaque étage d'un pipeline comme un état d'une
chaîne de Markov, avec des probabilités de transition mesurées
empiriquement. La distribution stationnaire révèle le goulot par le
calcul, pas par l'intuition.

Ce que le module fait :
1. CONSTRUCTION — bâtit la matrice de transition depuis des observations
   (liste de {etape, duree_s, octets, succes}). Les passages sont
   découpés en runs ; un échec mène à ECHEC, une fin de run réussie
   mène à TERMINE.
2. STATIONNAIRE — distribution stationnaire par itération de puissance,
   convergence vérifiée (norme L1 < tolérance).
3. GOULOT — l'état (hors absorbants) qui concentre la plus grande
   fraction du TEMPS (stationnaire pondérée par les durées moyennes).
4. ABSORPTION — temps moyen jusqu'à TERMINE depuis un état de départ
   (résolution du système linéaire, durées incluses).
5. SIMULATION — analyse de sensibilité : « si on multiplie le débit de
   l'étage X par k, quel gain sur le temps total ? ».

Hypothèses (à connaître avant d'interpréter) :
- MARKOVIANITÉ : la prochaine étape ne dépend que de l'étape courante,
  pas de l'historique. Vrai pour un pipeline séquentiel simple ; faux
  si des effets mémoire existent (cache chaud, échauffement).
- STATIONNARITÉ : les probabilités de transition sont stables dans le
  temps. Si le système dérive (montée en charge), reconstruire.
- Les durées moyennes par état sont supposées indépendantes de la
  trajectoire (pas de corrélation durée-état-suivant).
- Petit échantillon = incertitude : la matrice empirique n'est fiable
  que si chaque état a été observé assez souvent (voir `fiabilite`).

Limites honnêtes :
- Ne prédit pas les pannes inédites (jamais observées = probabilité 0).
- Ne modélise pas la concurrence (un seul créneau) ni les files d'attente.
- Le goulot « calculé » vaut ce que valent les observations.

Zéro dépendance externe : stdlib uniquement. Python 3.11 compatible.
"""

import json
from datetime import datetime, timezone

#: États absorbants réservés (convention).
TERMINE = "TERMINE"
ECHEC = "ECHEC"
ABSORBANTS = (TERMINE, ECHEC)

#: Tolérance de convergence de l'itération de puissance (norme L1).
TOL_STATIONNAIRE = 1e-10

#: Itérations maximales avant d'abandonner la convergence.
MAX_ITER_STATIONNAIRE = 10000


def _maintenant_iso():
    """Horodatage UTC ISO-8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ────────────────────────────────────────────────────────
# 1. CONSTRUCTION depuis les observations
# ────────────────────────────────────────────────────────

def _decouper_runs(observations, ordre):
    """Découpe la liste plate d'observations en runs.

    Un nouveau run commence quand l'étape courante apparaît AVANT
    l'étape précédente dans `ordre` (retour en arrière = nouveau
    passage dans le pipeline). Retourne une liste de runs, chacun
    étant une liste d'observations.
    """
    pos = {etape: i for i, etape in enumerate(ordre)}
    runs = []
    courant = []
    precedent_pos = None
    for obs in observations:
        etape = obs.get("etape")
        p = pos.get(etape)
        if p is None:
            # Étape inconnue : on l'ignore plutôt que de corrompre.
            continue
        if courant and precedent_pos is not None and p <= precedent_pos:
            runs.append(courant)
            courant = []
        courant.append(obs)
        precedent_pos = p
    if courant:
        runs.append(courant)
    return runs


def construire(observations, ordre=None):
    """Construit une chaîne de Markov depuis des observations.

    observations : liste de dicts {etape, duree_s, octets?, succes?}.
    ordre : ordre canonique des étapes (inféré de la première apparition
    si absent).

    Retourne un dict chaîne sérialisable en JSON :
    {
      "etats": [...], "transitions": {i: {j: proba}},
      "durees_moyennes": {etat: secondes},
      "comptes": {i: {j: n}}, "observations_n": n, "construit_iso": ...
    }

    Règles de transition :
    - étape réussie suivie d'une autre étape du même run → vers celle-ci ;
    - étape réussie en fin de run → TERMINE ;
    - étape en échec → ECHEC (le run s'arrête là) ;
    - TERMINE et ECHEC sont absorbants (bouclent sur eux-mêmes).
    """
    if ordre is None:
        ordre = []
        for obs in observations:
            etape = obs.get("etape")
            if etape and etape not in ordre:
                ordre.append(etape)
    if not ordre:
        raise ValueError("aucune étape observable : observations vides ?")

    runs = _decouper_runs(observations, ordre)

    comptes = {}          # comptes[i][j] = nombre de transitions i -> j
    durees = {}           # durees[i] = liste des durées observées

    def _compter(i, j):
        comptes.setdefault(i, {}).setdefault(j, 0)
        comptes[i][j] += 1

    for run in runs:
        for k, obs in enumerate(run):
            etape = obs["etape"]
            duree = float(obs.get("duree_s", 0.0) or 0.0)
            durees.setdefault(etape, []).append(duree)
            succes = obs.get("succes", True)
            if not succes:
                _compter(etape, ECHEC)
                break  # le run s'arrête sur l'échec
            if k + 1 < len(run):
                _compter(etape, run[k + 1]["etape"])
            else:
                _compter(etape, TERMINE)

    # États : ordre + absorbants (toujours présents, même sans observation).
    etats = list(ordre)
    for absorbant in ABSORBANTS:
        if absorbant not in etats:
            etats.append(absorbant)
    for absorbant in ABSORBANTS:
        _compter(absorbant, absorbant)

    # Normalisation en probabilités.
    transitions = {}
    for i in etats:
        total = sum(comptes.get(i, {}).values())
        transitions[i] = {}
        if total > 0:
            for j, n in comptes[i].items():
                transitions[i][j] = n / total
        else:
            # État jamais observé en départ : absorbant par défaut
            # (ne pas inventer de transitions).
            transitions[i] = {i: 1.0}

    durees_moyennes = {}
    for etat in etats:
        vals = durees.get(etat, [])
        durees_moyennes[etat] = sum(vals) / len(vals) if vals else 0.0

    return {
        "etats": etats,
        "transitions": transitions,
        "durees_moyennes": durees_moyennes,
        "comptes": {i: dict(v) for i, v in comptes.items()},
        "observations_n": len(observations),
        "runs_n": len(runs),
        "construit_iso": _maintenant_iso(),
    }


def fiabilite(chaine, seuil_min=5):
    """États dont le nombre de départs observés est sous le seuil.

    Retourne {etat: n_départs}. Un état peu observé rend sa ligne de
    transition fragile — le signaler, pas le masquer.
    """
    fragiles = {}
    for etat in chaine["etats"]:
        if etat in ABSORBANTS:
            continue  # absorbants par construction, pas fragiles
        n = sum(chaine["comptes"].get(etat, {}).values())
        if n < seuil_min:
            fragiles[etat] = n
    return fragiles


# ────────────────────────────────────────────────────────
# 2. DISTRIBUTION STATIONNAIRE (itération de puissance)
# ────────────────────────────────────────────────────────

def distribution_stationnaire(chaine, tol=TOL_STATIONNAIRE,
                              max_iter=MAX_ITER_STATIONNAIRE):
    """Distribution stationnaire par itération de puissance.

    Retourne {"distribution": {etat: proba}, "converge": bool,
    "iterations": n, "residu_l1": float}.

    Note : avec des états absorbants, la masse se concentre sur les
    absorbants. Pour l'analyse des goulots TRANSITOIRES, préférer
    `fraction_temps` ou `temps_absorption_moyen`.
    """
    etats = chaine["etats"]
    P = chaine["transitions"]
    n = len(etats)
    # Départ uniforme.
    pi = {e: 1.0 / n for e in etats}
    converge = False
    residu = float("inf")
    iterations = 0
    for iterations in range(1, max_iter + 1):
        suivant = {e: 0.0 for e in etats}
        for i in etats:
            pi_i = pi[i]
            if pi_i == 0.0:
                continue
            for j, p in P.get(i, {}).items():
                suivant[j] = suivant.get(j, 0.0) + pi_i * p
        residu = sum(abs(suivant[e] - pi[e]) for e in etats)
        pi = suivant
        if residu < tol:
            converge = True
            break
    return {
        "distribution": pi,
        "converge": converge,
        "iterations": iterations,
        "residu_l1": residu,
    }


def fraction_temps(chaine, distribution):
    """Fraction du TEMPS passée dans chaque état.

    pondération : pi_i * d_i / somme(pi_j * d_j). C'est cette mesure
    (pas la stationnaire brute) qui désigne le goulot d'un pipeline :
    un état rare mais très lent peut dominer le temps total.
    """
    durees = chaine["durees_moyennes"]
    poids = {e: distribution.get(e, 0.0) * durees.get(e, 0.0)
             for e in chaine["etats"]}
    total = sum(poids.values())
    if total <= 0:
        return {e: 0.0 for e in chaine["etats"]}
    return {e: w / total for e, w in poids.items()}


def _inverser_matrice(A):
    """Inverse une matrice carrée par Gauss-Jordan avec pivot partiel.

    Lève ValueError si singulière.
    """
    n = len(A)
    # [A | I].
    M = [list(A[i]) + [1.0 if i == j else 0.0 for j in range(n)]
         for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("matrice singulière : inversion impossible")
        M[col], M[piv] = M[piv], M[col]
        piv_val = M[col][col]
        for c in range(2 * n):
            M[col][c] /= piv_val
        for r in range(n):
            if r != col and abs(M[r][col]) > 1e-15:
                facteur = M[r][col]
                for c in range(2 * n):
                    M[r][c] -= facteur * M[col][c]
    return [row[n:] for row in M]


def sejours_moyens(chaine, depart, absorbants=ABSORBANTS):
    """Temps moyen passé dans chaque état avant absorption, depuis `depart`.

    Via la matrice fondamentale N = (I - Q)^{-1} : le temps moyen en
    j vaut N[depart][j] × d_j. C'est la bonne mesure du goulot pour
    une chaîne ABSORBANTE (un seul passage dans le pipeline), là où
    la stationnaire s'écrase sur les absorbants.

    Retourne {"sejours": {etat: secondes}, "total_s": float}.
    """
    etats = chaine["etats"]
    P = chaine["transitions"]
    durees = chaine["durees_moyennes"]
    absorbants = set(absorbants)
    transients = [e for e in etats if e not in absorbants]
    if depart in absorbants:
        return {"sejours": {e: 0.0 for e in etats}, "total_s": 0.0}
    if depart not in transients:
        raise ValueError("état de départ inconnu : %r" % depart)
    idx = {e: k for k, e in enumerate(transients)}
    n = len(transients)
    # I - Q.
    IMQ = [[0.0] * n for _ in range(n)]
    for e in transients:
        i = idx[e]
        IMQ[i][i] = 1.0
        for j, p in P.get(e, {}).items():
            if j in idx:
                IMQ[i][idx[j]] -= p
    N = _inverser_matrice(IMQ)
    ligne = N[idx[depart]]
    sejours = {}
    for e in transients:
        sejours[e] = ligne[idx[e]] * durees.get(e, 0.0)
    for a in absorbants:
        sejours[a] = 0.0
    return {"sejours": sejours, "total_s": sum(sejours.values())}


def identifier_goulot(chaine, distribution=None, exclure_absorbants=True):
    """Le goulot : l'état qui concentre la plus grande fraction du temps.

    Deux régimes :
    - Chaîne RÉCURRENTE (masse stationnaire sur les transients) :
      fraction du temps = pi_i × d_i / Σ pi_j × d_j.
    - Chaîne ABSORBANTE (masse écrasée sur TERMINE/ECHEC) : temps de
      séjour moyens depuis le premier transient (matrice fondamentale).

    Retourne {"goulot": etat, "fraction_temps": float,
    "fractions": {etat: fraction}, "regime": "recurrent"|"absorbant"}.
    """
    if distribution is None:
        distribution = distribution_stationnaire(chaine)["distribution"]
    candidats = [e for e in chaine["etats"]
                 if not (exclure_absorbants and e in ABSORBANTS)]
    if not candidats:
        candidats = list(chaine["etats"])
    masse_transients = sum(distribution.get(e, 0.0) for e in candidats)
    if masse_transients < 1e-6 and candidats:
        # Régime absorbant : séjours moyens depuis le premier transient.
        regime = "absorbant"
        sej = sejours_moyens(chaine, candidats[0])
        total = sej["total_s"]
        fractions = ({e: sej["sejours"].get(e, 0.0) / total
                      for e in candidats} if total > 0
                     else {e: 0.0 for e in candidats})
    else:
        regime = "recurrent"
        fractions = fraction_temps(chaine, distribution)
        fractions = {e: fractions.get(e, 0.0) for e in candidats}
    goulot = max(candidats, key=lambda e: fractions.get(e, 0.0))
    return {
        "goulot": goulot,
        "fraction_temps": fractions.get(goulot, 0.0),
        "fractions": fractions,
        "regime": regime,
    }


# ────────────────────────────────────────────────────────
# 3. TEMPS MOYEN D'ABSORPTION
# ────────────────────────────────────────────────────────

def _resoudre_lineaire(A, b):
    """Résout A·x = b par élimination de Gauss avec pivot partiel.

    A : liste de listes (n×n), b : liste (n). Retourne x (liste).
    Lève ValueError si le système est singulier.
    """
    n = len(b)
    # Matrice augmentée.
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        # Pivot partiel.
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("système singulier : absorption non définie "
                             "(état transient sans issue ?)")
        M[col], M[piv] = M[piv], M[col]
        # Élimination.
        for r in range(col + 1, n):
            facteur = M[r][col] / M[col][col]
            for c in range(col, n + 1):
                M[r][c] -= facteur * M[col][c]
    # Remontée.
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / M[i][i]
    return x


def temps_absorption_moyen(chaine, depart, absorbants=ABSORBANTS):
    """Temps moyen (durées incluses) jusqu'à absorption depuis `depart`.

    Résout t_i = d_i + Σ_j P[i][j]·t_j pour les transients,
    t_a = 0 pour les absorbants. Retourne {"temps_s": float,
    "detail": {etat: temps_restant}}.

    Lève ValueError si le système est singulier (un transient ne peut
    jamais atteindre un absorbant — chaîne mal formée).
    """
    etats = chaine["etats"]
    P = chaine["transitions"]
    durees = chaine["durees_moyennes"]
    absorbants = set(absorbants)
    transients = [e for e in etats if e not in absorbants]
    if depart in absorbants:
        return {"temps_s": 0.0, "detail": {e: 0.0 for e in etats}}
    idx = {e: k for k, e in enumerate(transients)}
    n = len(transients)
    # (I - Q)·t = d
    A = [[0.0] * n for _ in range(n)]
    b = [0.0] * n
    for e in transients:
        i = idx[e]
        A[i][i] = 1.0
        b[i] = durees.get(e, 0.0)
        for j, p in P.get(e, {}).items():
            if j in idx:
                A[i][idx[j]] -= p
            # vers un absorbant : t_j = 0, rien à soustraire.
    t_trans = _resoudre_lineaire(A, b)
    detail = {e: t_trans[idx[e]] for e in transients}
    for a in absorbants:
        detail[a] = 0.0
    return {"temps_s": detail.get(depart, 0.0), "detail": detail}


# ────────────────────────────────────────────────────────
# 4. SIMULATION : analyse de sensibilité au débit
# ────────────────────────────────────────────────────────

def simuler_debit(chaine, etape, facteur):
    """« Si on multiplie le débit de `etape` par `facteur`, quel gain ? »

    Doubler le débit = diviser la durée moyenne par 2. La structure
    markovienne (routage, échecs) est inchangée — seule la durée de
    l'étage visé bouge. Retourne :
    {"etape": ..., "facteur": ..., "temps_avant_s": ..., "temps_apres_s": ...,
     "gain_relatif": float (0..1), "nouveau_goulot": ...}.
    Le temps total considéré est le temps moyen d'absorption depuis le
    premier état non-absorbant.
    """
    if facteur <= 0:
        raise ValueError("facteur doit être > 0")
    if etape not in chaine["etats"]:
        raise ValueError("étape inconnue : %r" % etape)
    departs = [e for e in chaine["etats"] if e not in ABSORBANTS]
    if not departs:
        raise ValueError("aucun état transient")
    depart = departs[0]

    avant = temps_absorption_moyen(chaine, depart)["temps_s"]

    # Chaîne modifiée : durée de l'étage divisée par le facteur.
    modifiee = {
        "etats": list(chaine["etats"]),
        "transitions": {i: dict(v)
                        for i, v in chaine["transitions"].items()},
        "durees_moyennes": dict(chaine["durees_moyennes"]),
    }
    modifiee["durees_moyennes"][etape] = (
        modifiee["durees_moyennes"].get(etape, 0.0) / facteur)

    apres = temps_absorption_moyen(modifiee, depart)["temps_s"]
    gain = (avant - apres) / avant if avant > 0 else 0.0
    dist = distribution_stationnaire(modifiee)["distribution"]
    nouveau = identifier_goulot(modifiee, dist)

    return {
        "etape": etape,
        "facteur": facteur,
        "temps_avant_s": avant,
        "temps_apres_s": apres,
        "gain_relatif": gain,
        "nouveau_goulot": nouveau["goulot"],
        "nouveau_goulot_fraction": nouveau["fraction_temps"],
    }


def comparer_scenarios(chaine, facteurs=(2.0,)):
    """Sensibilité pour chaque étage transient : quel étage accélérer ?

    Retourne la liste des scénarios triés par gain décroissant.
    C'est la réponse calculée à « où investir l'optimisation ? ».
    """
    scenarios = []
    for etape in chaine["etats"]:
        if etape in ABSORBANTS:
            continue
        for facteur in facteurs:
            try:
                scenarios.append(simuler_debit(chaine, etape, facteur))
            except ValueError:
                continue
    scenarios.sort(key=lambda s: s["gain_relatif"], reverse=True)
    return scenarios


# ────────────────────────────────────────────────────────
# 5. PERSISTANCE
# ────────────────────────────────────────────────────────

def sauvegarder(chaine, chemin):
    """Écrit la chaîne en JSON (atomique : tmp + replace)."""
    import os
    import tempfile
    dossier = os.path.dirname(os.path.abspath(chemin)) or "."
    os.makedirs(dossier, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dossier, prefix=".tmp-",
                               suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(chaine, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return chemin


def charger(chemin):
    """Lit une chaîne depuis un JSON. Lève ValueError si invalide."""
    with open(chemin, encoding="utf-8") as fh:
        chaine = json.load(fh)
    if not isinstance(chaine, dict) or "etats" not in chaine \
            or "transitions" not in chaine:
        raise ValueError("fichier de chaîne invalide : %s" % chemin)
    return chaine


# ────────────────────────────────────────────────────────
# 6. RENDUS CONSOLE
# ────────────────────────────────────────────────────────

def rendre_matrice_console(chaine):
    """Matrice de transition en texte."""
    etats = chaine["etats"]
    P = chaine["transitions"]
    lignes = ["Matrice de transition (lignes = départ, colonnes = arrivée) :"]
    entete = "          " + "".join("%10s" % e[:9] for e in etats)
    lignes.append(entete)
    for i in etats:
        ligne = "%-10s" % i[:9]
        for j in etats:
            ligne += "%10.3f" % P.get(i, {}).get(j, 0.0)
        lignes.append(ligne)
    return "\n".join(lignes)


def rendre_diagnostic_console(chaine):
    """Diagnostic complet : matrice, stationnaire, goulot, absorption."""
    lignes = []
    lignes.append(rendre_matrice_console(chaine))
    lignes.append("")
    lignes.append("Durées moyennes par état (s) :")
    for e in chaine["etats"]:
        lignes.append("  %-12s %10.2f s" % (e, chaine["durees_moyennes"].get(e, 0.0)))
    lignes.append("")
    stat = distribution_stationnaire(chaine)
    lignes.append("Distribution stationnaire "
                  "(converge=%s, %d itérations, résidu=%.2e) :"
                  % (stat["converge"], stat["iterations"], stat["residu_l1"]))
    for e in chaine["etats"]:
        lignes.append("  %-12s %8.4f" % (e, stat["distribution"].get(e, 0.0)))
    lignes.append("")
    goulot = identifier_goulot(chaine, stat["distribution"])
    lignes.append("GOULOT (régime %s, hors absorbants) : %s (%.1f %%)"
                  % (goulot["regime"], goulot["goulot"],
                     100.0 * goulot["fraction_temps"]))
    for e, f in sorted(goulot["fractions"].items(),
                       key=lambda kv: kv[1], reverse=True):
        lignes.append("  %-12s %6.1f %%" % (e, 100.0 * f))
    lignes.append("")
    departs = [e for e in chaine["etats"] if e not in ABSORBANTS]
    if departs:
        try:
            t = temps_absorption_moyen(chaine, departs[0])
            lignes.append("Temps moyen d'absorption depuis %s : %.2f s"
                          % (departs[0], t["temps_s"]))
        except ValueError as exc:
            lignes.append("Absorption non définie : %s" % exc)
    lignes.append("")
    fragiles = fiabilite(chaine)
    if fragiles:
        lignes.append("États peu observés (< 5 départs) — matrice fragile :")
        for e, n in fragiles.items():
            lignes.append("  %-12s %d départ(s)" % (e, n))
    else:
        lignes.append("Fiabilité : tous les états ont ≥ 5 départs observés.")
    return "\n".join(lignes)


def rendre_simulation_console(scenario):
    """Rendu d'un scénario de sensibilité."""
    return (
        "Scénario : débit de %(etape)s × %(facteur).1f\n"
        "  Temps total avant : %(temps_avant_s)8.2f s\n"
        "  Temps total après : %(temps_apres_s)8.2f s\n"
        "  Gain relatif      : %(gain_pct)8.1f %%\n"
        "  Nouveau goulot    : %(nouveau_goulot)s (%(nouveau_pct).1f %%)"
        % {"etape": scenario["etape"], "facteur": scenario["facteur"],
           "temps_avant_s": scenario["temps_avant_s"],
           "temps_apres_s": scenario["temps_apres_s"],
           "gain_pct": 100.0 * scenario["gain_relatif"],
           "nouveau_goulot": scenario["nouveau_goulot"],
           "nouveau_pct": 100.0 * scenario["nouveau_goulot_fraction"]}
    )


# ────────────────────────────────────────────────────────
# 7. EXEMPLE : observations réelles mesurées le 2026-10-05
# ────────────────────────────────────────────────────────

def exemple_observations_ast():
    """Jeu d'observations synthétisé depuis nos mesures réelles.

    - EXPORT : 474 Mo en 141 s → 3,36 Mo/s (mesuré 2026-10-05).
    - LECTURE : validateur Python ~3,0 Mo/s (14 600 décl. en 231 s).
    - ELABORATION : créneau Lean très variable (12 s à 43 min observés).
    - ANALYSE : phi-complexity sur données parsées (ordre de grandeur).
    Tailles ramenées à un fichier de ~770 Mo (core Lean complet).
    """
    return [
        # Run 1 : nominal complet.
        {"etape": "ELABORATION", "duree_s": 180.0, "octets": 0,
         "succes": True},
        {"etape": "EXPORT", "duree_s": 229.0, "octets": 807288832,
         "succes": True},
        {"etape": "LECTURE", "duree_s": 257.0, "octets": 807288832,
         "succes": True},
        {"etape": "ANALYSE", "duree_s": 45.0, "octets": 807288832,
         "succes": True},
        # Run 2 : export rapide (Array), lecture OK.
        {"etape": "ELABORATION", "duree_s": 95.0, "octets": 0,
         "succes": True},
        {"etape": "EXPORT", "duree_s": 141.0, "octets": 474767360,
         "succes": True},
        {"etape": "LECTURE", "duree_s": 160.0, "octets": 474767360,
         "succes": True},
        {"etape": "ANALYSE", "duree_s": 30.0, "octets": 474767360,
         "succes": True},
        # Run 3 : élaboration longue, export échoué (tronqué).
        {"etape": "ELABORATION", "duree_s": 2580.0, "octets": 0,
         "succes": True},
        {"etape": "EXPORT", "duree_s": 600.0, "octets": 807288832,
         "succes": False},
        # Run 4 : petit fichier, rapide.
        {"etape": "ELABORATION", "duree_s": 12.0, "octets": 0,
         "succes": True},
        {"etape": "EXPORT", "duree_s": 8.0, "octets": 25000000,
         "succes": True},
        {"etape": "LECTURE", "duree_s": 9.0, "octets": 25000000,
         "succes": True},
        {"etape": "ANALYSE", "duree_s": 5.0, "octets": 25000000,
         "succes": True},
        # Run 5 : nominal.
        {"etape": "ELABORATION", "duree_s": 210.0, "octets": 0,
         "succes": True},
        {"etape": "EXPORT", "duree_s": 240.0, "octets": 807288832,
         "succes": True},
        {"etape": "LECTURE", "duree_s": 270.0, "octets": 807288832,
         "succes": True},
        {"etape": "ANALYSE", "duree_s": 50.0, "octets": 807288832,
         "succes": True},
    ]


ORDRE_AST = ["ELABORATION", "EXPORT", "LECTURE", "ANALYSE"]
