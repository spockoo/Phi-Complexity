#!/usr/bin/env python3
"""Propagation d'impact sur un graphe de dépendances — analyseur d'impact (chantier 2/5).

Stdlib uniquement. Consomme le contrat du chantier 1 (`graphe.py`) par
duck-typing : tout objet exposant `noeuds()`, `successeurs(nid)` et
`predecesseurs(nid)` convient.

=====================================================================
DÉFINITIONS FORMELLES  (à lire avant le code)
=====================================================================

Soit G = (V, E) un graphe orienté de dépendances de code Python.
Une arête u → v signifie : « u UTILISE v » (u appelle v, u importe v, …).
- `successeurs(u)`  = { v | u → v } : ce que u utilise (ses dépendances).
- `predecesseurs(u)` = { w | w → u } : ce qui utilise u (ses dépendants).

1. CHEMIN D'IMPACT (sens avant).
   Une suite (n₀, n₁, …, nₖ), k ≥ 1, telle que nᵢ₊₁ ∈ predecesseurs(nᵢ)
   pour tout i. Le nœud nₖ est dit *impacté à profondeur k par n₀* :
   changer n₀ peut forcer à reconsidérer nₖ, via la chaîne de
   dépendances nₖ ⇝ … ⇝ n₁ ⇝ n₀. La *profondeur* est la longueur k du
   chemin (nombre d'arêtes). Le *chemin d'impact* est la liste des
   identifiants [n₀, n₁, …, nₖ] ; on retient pour chaque nœud impacté
   le chemin le PLUS COURT (voir règle 3).

2. SENS DE PROPAGATION.
   - `impact_avant(n)` = fermeture transitive des PRÉDÉCESSEURS de n :
     tous les nœuds qui dépendent de n, directement ou non.
     « Si je change n, qu'est-ce qui casse ? »
   - `dependances_arriere(n)` = fermeture transitive des SUCCESSEURS
     de n : tout ce dont n dépend, directement ou non.
     « De quoi n dépend-il ? »

3. RÈGLE DE CYCLE.
   Le parcours est un BFS par niveaux. Un nœud DÉJÀ VISITÉ n'est
   jamais ré-expandé : cela garantit la terminaison sur les graphes
   cycliques. Comme le BFS découvre chaque nœud au niveau le plus
   petit possible, le chemin conservé pour un nœud visité est TOUJOURS
   un plus court chemin d'impact depuis la source (en cas d'égalité
   de longueur, le premier découvert à voisins triés par identifiant,
   donc déterministe). Le nœud source lui-même n'apparaît jamais dans
   les résultats, même s'il est atteignable via un cycle.

4. TRONCATURE.
   `profondeur_max` borne la longueur des chemins explorés : aucun
   nœud à profondeur > profondeur_max n'est retourné.
   `tronque = True`  ⟺  il existe un nœud visité u de profondeur
   exactement profondeur_max possédant un voisin v NON visité :
   l'arête u → v aurait produit v à profondeur profondeur_max + 1,
   la propagation a donc été coupée par la borne.
   `profondeur_max_atteinte` = plus grande profondeur parmi les
   nœuds retournés (0 si aucun).

Complexité : O(V + E) temps, O(V) mémoire.

Clés retournées : « noeud », « impactes », « profondeur_max_atteinte »,
« tronque » (contrat chantier 2), plus « D », « P », « impactes_ids »
(compatibilité chantier 3/risque.py — purement additives).
"""

from collections import deque


# ---------------------------------------------------------------------------
# moteur commun
# ---------------------------------------------------------------------------

def _ids_voisins(paires):
    """Extrait les identifiants d'une liste de (nid, attributs)."""
    return [nid for nid, _attributs in paires]


def _fermeture(graphe, nid, profondeur_max, fonction_voisins):
    """BFS par niveaux sur `fonction_voisins` (prédécesseurs ou successeurs).

    Retourne le dictionnaire de résultat au format du contrat.
    """
    # --- validation des entrées ------------------------------------------
    if not isinstance(profondeur_max, int) or isinstance(profondeur_max, bool):
        raise ValueError(
            f"profondeur_max doit être un entier, reçu : {profondeur_max!r}"
        )
    if profondeur_max < 0:
        raise ValueError(
            f"profondeur_max doit être >= 0, reçu : {profondeur_max}"
        )
    identifiants_connus = {n["id"] for n in graphe.noeuds()}
    if nid not in identifiants_connus:
        raise ValueError(
            f"nœud inconnu : {nid!r} "
            f"({len(identifiants_connus)} nœuds dans le graphe)"
        )

    # --- BFS par niveaux ---------------------------------------------------
    profondeur = {nid: 0}          # nid -> plus courte profondeur connue
    chemin = {nid: [nid]}          # nid -> plus court chemin d'impact connu
    visites = {nid}
    resultats = []

    niveau_courant = deque([nid])
    niveau = 0
    while niveau_courant and niveau < profondeur_max:
        niveau += 1
        niveau_suivant = deque()
        for u in niveau_courant:
            voisins = sorted(_ids_voisins(fonction_voisins(graphe, u)))
            for v in voisins:
                if v not in visites:          # règle de cycle : jamais ré-expandé
                    visites.add(v)
                    profondeur[v] = niveau    # BFS => plus court chemin (déf. 3)
                    chemin[v] = chemin[u] + [v]
                    resultats.append(
                        {"id": v, "profondeur": niveau, "chemin": chemin[v]}
                    )
                    niveau_suivant.append(v)
        niveau_courant = niveau_suivant

    # --- détection de troncature (déf. 4) ----------------------------------
    tronque = False
    for u, d in profondeur.items():
        if d == profondeur_max:
            for v in _ids_voisins(fonction_voisins(graphe, u)):
                if v not in visites:
                    tronque = True
                    break
        if tronque:
            break

    resultats.sort(key=lambda r: (r["profondeur"], r["id"]))
    profondeur_max_atteinte = max(
        (r["profondeur"] for r in resultats), default=0
    )

    return {
        "noeud": nid,
        "impactes": resultats,
        "profondeur_max_atteinte": profondeur_max_atteinte,
        "tronque": tronque,
        # Clés supplémentaires (compatibilité chantier 3 : risque.py lit
        # "D" et "P" ; elles n'altèrent pas le contrat ci-dessus) :
        "D": len(resultats),                 # = |I(n)| : nœuds impactés
        "P": profondeur_max_atteinte,        # = P(n) : profondeur max
        "impactes_ids": [r["id"] for r in resultats],  # ids seuls, triés
    }


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def impact_avant(graphe, nid: str, profondeur_max: int = 10) -> dict:
    """Nœuds AFFECTÉS si `nid` change : fermeture transitive des PRÉDÉCESSEURS.

    « Si je change cette fonction, qu'est-ce qui casse ? »
    Voir les définitions formelles en tête de module (§1–§4).
    """
    return _fermeture(graphe, nid, profondeur_max, _predecesseurs_ids)


def dependances_arriere(graphe, nid: str, profondeur_max: int = 10) -> dict:
    """Ce dont `nid` DÉPEND : fermeture transitive des SUCCESSEURS.

    Même format de retour que `impact_avant` (la clé « impactes » désigne
    ici les dépendances atteintes).
    """
    return _fermeture(graphe, nid, profondeur_max, _successeurs_ids)


def _predecesseurs_ids(graphe, nid):
    return graphe.predecesseurs(nid)


def _successeurs_ids(graphe, nid):
    return graphe.successeurs(nid)
