#!/usr/bin/env python3
"""Parsing incrémental avec invalidation en cascade.

Mission RUCHE-PARSE-CACHE — disciple D3 (chantier 3/5).

Rôle : quand les octets sources d'une déclaration changent, re-parser
cette déclaration (via D2) et invalider en cascade tout ce qui en dépend
transitivement, d'après un graphe de dépendances extrait des AST eux-mêmes
(nœuds `const` des expressions).

Briques réutilisées (APIs réelles, pas devinées) :
  - ``parse_cache.cache_ast.ParseCache`` (D1) : ``put(nom, hash_src, ast)`` /
    ``get(nom, hash_src)`` ; clé = (nom, hash_src), hash = sha256 des octets.
  - ``parse_cache.invalidation`` (D2) : ``invalider(nom, cache)`` (supprime
    toutes les entrées d'un nom) et ``valider_ou_reparser(nom, octets_src,
    parse_fn, cache)`` (hit si le hash correspond, re-parse sinon).

CONTRATS D'ENTRÉE
-----------------
- ``declarations`` : itérable de paires ``(nom, ast_obj)`` ; ``nom`` est une
  chaîne (clé de cache D1, forme ``Name.toString`` pointée).
- ``ast_obj`` : AST d'une déclaration, sous l'une des deux formes RÉELLES
  inspectées dans ``fidelite/phiast_ast.py`` (jamais devinées) :
    1. dict ``{"type": expr, "value": expr | None}`` (``parse_decl`` fidélité) ;
    2. une expression nue (tuple/list).
  Une expression est un tuple/list dont le premier élément est un tag parmi
  ``bvar fvar mvar sort const app lam forallE letE lit-nat lit-str proj
  mdata`` ; un nœud ``const`` a la forme ``("const", segments, niveaux)``
  où ``segments`` = ``[("str", s) | ("num", n), ...]``.
  Toute autre forme lève ``FormatAstInattendu`` (échec bruyant).
- ``parse_fn`` : ``octets_src -> ast_obj`` (même format d'AST que ci-dessus).
- ``graphe`` : ``dict[str, set[str]]`` — pour chaque nom, les noms qu'il
  référence DIRECTEMENT. Construit par ``construire_graphe_dependances`` ;
  ``reparser_incremental`` le met à jour en place pour chaque nom (re-)parsé.
- ``cache`` : instance ``ParseCache`` (D1).

Pourquoi ``impact_analyse/propagation.py`` n'est PAS réutilisé ici
---------------------------------------------------------------
``impact_avant`` convient sémantiquement (fermeture des prédécesseurs) mais
son contrat ne convient pas : il exige le duck-type ``Graphe``
(``noeuds()`` / ``successeurs()`` / ``predecesseurs()`` en paires
``(nid, attributs)``) et une borne ``profondeur_max`` explicite, alors
qu'ici le graphe est un ``dict[str, set[str]]`` nu et que la cascade exige
une fermeture NON bornée. Un adaptateur coûterait un balayage O(V) par
nœud visité — soit O(V²) à l'échelle des ~200 000 déclarations des
fichiers PHIAST réels — contre O(V + E) pour le BFS direct ci-dessous
(index inverse construit une fois, ``visited set`` contre les cycles).
Le BFS direct est donc le choix exact, pas un refus de réutilisation.

LIMITES HONNÊTES — lire avant de faire confiance à la cascade
-------------------------------------------------------------
1. Exhaustivité de l'extraction : CONDITIONNELLE, jamais prouvée.
   Sont collectés : les nœuds ``const`` (références globales). Sont
   VOLONTAIREMENT exclus : le nom de structure des nœuds ``proj`` ;
   ``fvar``/``mvar`` (variables locales / métavariables : pas des
   déclarations) ; les noms de lieurs ``lam``/``forallE``/``letE``.
   Tout tag d'expression INCONNU lève ``TagExprInconnu`` au lieu d'être
   ignoré silencieusement : un futur constructeur porteur de références
   ne peut pas passer inaperçu — l'échec est bruyant (sagesse de Phidélia).
   Risque résiduel assumé : une référence portée par un nœud CONNU mais
   exclu (aujourd'hui : ``proj``) — voir point 3.
2. Le hash (D2) ne répare PAS une arête manquée.
   ``est_valide`` compare ``(nom, hash_src)`` : il détecte qu'un AST est
   périmé quand SES PROPRES octets ont changé. Il ne détecte pas qu'un
   dépendant B est sémantiquement périmé parce qu'une dépendance A a changé
   alors que les octets de B sont intacts — c'est exactement le travail de
   la cascade. Si l'extraction a raté l'arête B → A, B restera « valide »
   au sens du hash tout en étant sémantiquement périmé. Le garde-fou pour
   ce cas, au prochain accès complet, est une RE-PARSE COMPLÈTE périodique
   (toutes les déclarations, graphe reconstruit de zéro), qui ne dépend
   d'aucune arête préalablement extraite. Le hash seul ne suffit pas ;
   ne jamais prétendre le contraire.
3. ``ParseCache`` (D1) ne conserve PAS les octets sources : la clé est
   ``(nom, hash_src)`` et seul l'AST picklé est stocké. Le re-parse EAGER
   d'un dépendant n'est donc possible que si l'appelant fournit ses octets
   (paramètre ``sources`` de ``reparser_incremental``). Sans eux, les
   dépendants sont INVALIDÉS (sûr : aucun AST périmé ne peut être servi)
   et re-parsés PARESSEUSEMENT au prochain accès, quand l'appelant aura
   les octets en main (``valider_ou_reparser`` de D2).
4. Le graphe est une image, pas le territoire : figé au moment de sa
   construction, mis à jour en place par ``reparser_incremental`` pour
   chaque nom (re-)parsé. Toute mutation du cache hors de ce module
   (``put``/``invalider`` directs) peut le désynchroniser — en cas de
   doute, reconstruire via ``construire_graphe_dependances``.
5. Cycles : gérés par ``visited set`` (terminaison garantie). Une
   auto-référence est conservée dans le graphe (référence directe réelle)
   mais la source est exclue de ses propres dépendants transitifs
   (convention identique à ``impact_avant`` : la source n'apparaît jamais
   dans son propre résultat, même via un cycle).

stdlib uniquement.
"""

from collections import deque

from .cache_ast import calculer_hash_src
from .invalidation import invalider as _invalider_d2
from .invalidation import valider_ou_reparser as _valider_ou_reparser_d2

__all__ = [
    "ErreurIncremental",
    "TagExprInconnu",
    "FormatAstInattendu",
    "OctetsSourceManquants",
    "segments_vers_cle",
    "extraire_references_expr",
    "construire_graphe_dependances",
    "dependants_transitifs",
    "invalider_cascade",
    "reparser_incremental",
]


# ---------------------------------------------------------------------------
# erreurs
# ---------------------------------------------------------------------------

class ErreurIncremental(Exception):
    """Base des erreurs du parsing incrémental."""


class TagExprInconnu(ErreurIncremental):
    """L'extracteur a rencontré un nœud d'expression de tag inconnu.

    Levé au lieu d'ignorer silencieusement : un constructeur inconnu
    pourrait porter des références, et les manquer corromprait la cascade
    (voir LIMITES HONNÊTES §1).
    """


class FormatAstInattendu(ErreurIncremental):
    """La forme de l'AST ne correspond pas au contrat documenté."""


class OctetsSourceManquants(ErreurIncremental):
    """Re-parse EAGER impossible : les octets sources d'un dépendant
    invalidé n'ont été fournis ni par le cache ni par l'appelant."""


# ---------------------------------------------------------------------------
# 1. extraction des références depuis l'AST
# ---------------------------------------------------------------------------

# Tags d'expression RÉELS de fidelite/phiast_ast.py::parse_expr (inspectés,
# pas devinés). Tout autre tag lève TagExprInconnu (jamais ignoré).
_TAGS_EXPR_CONNUS = frozenset({
    "bvar", "fvar", "mvar", "sort", "const", "app",
    "lam", "forallE", "letE", "lit-nat", "lit-str", "proj", "mdata",
})


def segments_vers_cle(segments):
    """Convertit des segments de nom en clé pointée ``Name.toString``.

    ``[("str", "Lean"), ("num", 3)]`` → ``"Lean.3"``. Même convention que
    ``lecteur_phiast.py::parse_name(retenir=True)`` : segments joints par
    ``"."``, numéros rendus en décimal pur. ``[]`` (anonyme) → ``""``.
    """
    if not isinstance(segments, (list, tuple)):
        raise FormatAstInattendu(
            "segments de nom inattendus : %r (liste de paires "
            "('str'|'num', valeur) attendue)" % (segments,))
    parties = []
    for seg in segments:
        if (not isinstance(seg, (list, tuple)) or len(seg) != 2
                or seg[0] not in ("str", "num")):
            raise FormatAstInattendu(
                "segment de nom inattendu : %r" % (seg,))
        genre, valeur = seg
        if genre == "str":
            if not isinstance(valeur, str):
                raise FormatAstInattendu(
                    "segment 'str' non-chaîne : %r" % (valeur,))
            parties.append(valeur)
        else:
            if not isinstance(valeur, int) or isinstance(valeur, bool):
                raise FormatAstInattendu(
                    "segment 'num' non-entier : %r" % (valeur,))
            parties.append(str(valeur))
    return ".".join(parties)


def _exiger_arite(noeud, arite):
    if len(noeud) != arite:
        raise FormatAstInattendu(
            "nœud %r : arité %d attendue, %d reçue"
            % (noeud[0], arite, len(noeud)))


def _collecter(expr, acc):
    """Parcourt une expression, ajoute les noms des nœuds `const` à `acc`.

    Seuls les `const` référencent des déclarations globales. Les `fvar` /
    `mvar` sont des variables locales / métavariables, les noms des lieurs
    ne sont pas des références. EXCLUSION DOCUMENTÉE : le nom de structure
    des nœuds `proj` n'est pas collecté (voir LIMITES HONNÊTES §1).
    """
    if not isinstance(expr, (list, tuple)) or len(expr) == 0:
        raise FormatAstInattendu(
            "nœud d'expression inattendu : %r" % (expr,))
    tag = expr[0]
    if not isinstance(tag, str) or tag not in _TAGS_EXPR_CONNUS:
        raise TagExprInconnu(
            "tag d'expression inconnu : %r — l'extracteur refuse d'ignorer "
            "silencieusement un nœud qu'il ne connaît pas (un constructeur "
            "inconnu pourrait porter des références et corrompre la "
            "cascade ; voir LIMITES HONNÊTES §1)" % (tag,))
    if tag == "const":
        # ("const", segments, niveaux) — les niveaux ne référencent jamais
        # de déclarations (que des variables de niveau), on ne les parcourt
        # pas.
        if len(expr) < 2:
            raise FormatAstInattendu(
                "nœud 'const' sans nom : %r" % (expr,))
        acc.add(segments_vers_cle(expr[1]))
        return
    if tag == "app":
        _exiger_arite(expr, 3)
        _collecter(expr[1], acc)
        _collecter(expr[2], acc)
        return
    if tag in ("lam", "forallE"):
        _exiger_arite(expr, 5)
        _collecter(expr[2], acc)  # type
        _collecter(expr[3], acc)  # corps (expr[1] = nom du lieur : ignoré)
        return
    if tag == "letE":
        _exiger_arite(expr, 6)
        _collecter(expr[2], acc)
        _collecter(expr[3], acc)
        _collecter(expr[4], acc)
        return
    if tag == "proj":
        # ("proj", nom_structure, idx, expr) — le nom de la structure est
        # une vraie référence globale, VOLONTAIREMENT non collectée
        # (contrat de la mission : nœuds `const` uniquement).
        _exiger_arite(expr, 4)
        _collecter(expr[3], acc)
        return
    if tag == "mdata":
        _exiger_arite(expr, 2)
        _collecter(expr[1], acc)
        return
    # bvar, fvar, mvar, sort, lit-nat, lit-str : aucune référence globale.
    return


def extraire_references_expr(expr):
    """Noms de déclarations référencés DIRECTEMENT par une expression.

    Collecte exactement les nœuds ``("const", segments, niveaux)`` ;
    voir ``_collecter`` pour les exclusions documentées et
    LIMITES HONNÊTES §1 pour le risque résiduel.
    """
    trouvees = set()
    _collecter(expr, trouvees)
    return trouvees


def _exprs_de_ast(ast_obj):
    """Normalise un ast_obj en liste d'expressions à parcourir.

    Formes acceptées (contrat, § CONTRATS D'ENTRÉE) :
      - dict ``{"type": expr, "value": expr | None}`` ;
      - une expression nue (tuple/list).
    """
    if isinstance(ast_obj, dict):
        if "type" not in ast_obj:
            raise FormatAstInattendu(
                "dict d'AST sans clé 'type' : clés reçues %s"
                % sorted(ast_obj))
        exprs = [ast_obj["type"]]
        if ast_obj.get("value") is not None:
            exprs.append(ast_obj["value"])
        return exprs
    if isinstance(ast_obj, (list, tuple)):
        return [ast_obj]
    raise FormatAstInattendu(
        "ast_obj inattendu : %r (dict {'type','value'} ou expression "
        "attendue)" % (ast_obj,))


def construire_graphe_dependances(declarations):
    """Construit ``{nom: {noms référencés DIRECTEMENT}}``.

    ``declarations`` : itérable de paires ``(nom, ast_obj)`` (un dict
    ``{nom: ast_obj}`` est aussi accepté). Chaque nom déclaré est une clé
    du graphe, même sans référence (ensemble vide). Les références vers
    des noms non déclarés (ex. préambule Lean) sont conservées dans les
    ensembles : ce sont de vraies arêtes, même si leur cible n'est pas
    un nœud du graphe. Les auto-références sont conservées.
    """
    if isinstance(declarations, dict):
        declarations = declarations.items()
    graphe = {}
    for paire in declarations:
        try:
            nom, ast_obj = paire
        except (TypeError, ValueError):
            raise FormatAstInattendu(
                "paire (nom, ast_obj) attendue, reçu : %r" % (paire,))
        if not isinstance(nom, str):
            raise FormatAstInattendu(
                "nom de déclaration non-chaîne : %r" % (nom,))
        refs = set()
        for expr in _exprs_de_ast(ast_obj):
            _collecter(expr, refs)
        graphe[nom] = refs
    return graphe


# ---------------------------------------------------------------------------
# 2. dépendants transitifs (BFS sur les arêtes inverses, cycles gérés)
# ---------------------------------------------------------------------------

def dependants_transitifs(nom, graphe):
    """Tous les noms qui dépendent (transitivement) de ``nom``.

    BFS sur l'index inverse (arête v → u lue comme « u dépend de v »),
    construit en O(V + E). ``visited set`` : terminaison garantie sur les
    graphes cycliques. La source est exclue du résultat, même via un cycle
    (même convention que ``impact_avant``). Un nom inconnu du graphe
    retourne un ensemble vide (pas d'exception).
    """
    inverse = {}
    for u, refs in graphe.items():
        for v in refs:
            inverse.setdefault(v, set()).add(u)
    vus = {nom}
    resultat = set()
    file = deque([nom])
    while file:
        courant = file.popleft()
        for pred in inverse.get(courant, ()):
            if pred not in vus:
                vus.add(pred)
                resultat.add(pred)
                file.append(pred)
    return resultat


# ---------------------------------------------------------------------------
# 3. invalidation en cascade (via D2)
# ---------------------------------------------------------------------------

def invalider_cascade(noms_modifies, graphe, cache):
    """Invalide les noms modifiés + tous leurs dépendants transitifs.

    Utilise ``invalider(nom, cache)`` de D2 (suppression ciblée par nom,
    tous hashs confondus). Ordre d'invalidation déterministe (trié).
    Retourne l'ensemble des noms invalidés (pour le rapport).
    """
    cibles = set(noms_modifies)
    for nom in list(cibles):
        cibles |= dependants_transitifs(nom, graphe)
    for nom in sorted(cibles):
        _invalider_d2(nom, cache)
    return cibles


# ---------------------------------------------------------------------------
# 4. re-parse incrémental
# ---------------------------------------------------------------------------

def reparser_incremental(modifications, graphe, cache, parse_fn,
                         sources=None):
    """Re-parse incrémental : modifiés d'abord, cascade ensuite.

    1. Pour chaque nom modifié : ``valider_ou_reparser`` (D2) — re-parse
       seulement si les octets ont changé (comparaison par hash), sinon
       hit sur l'AST en cache. Le graphe est mis à jour en place pour ce
       nom (ses arêtes ont pu changer).
    2. ``invalider_cascade`` sur l'ensemble des noms modifiés (graphe à
       jour) : les dépendants transitifs sont invalidés dans le cache.
       La sémantique D2 étant par NOM (tous hashs confondus), la cascade
       supprime aussi les entrées fraîches que l'étape 1 venait d'écrire.
    3. Dépendants invalidés : re-parse EAGER via ``valider_ou_reparser``
       si ``sources`` fournit leurs octets (``dict nom -> octets_src``),
       dans un ordre déterministe (trié) ; sinon ils restent invalidés et
       seront re-parsés PARESSEUSEMENT au prochain accès avec les octets
       en main — aucun AST périmé ne peut être servi entre-temps
       (voir LIMITES HONNÊTES §3).
    4. DÉCISION D3 (documentée, réversible) : les entrées fraîches des
       noms modifiés, supprimées par la cascade à l'étape 2, sont
       RESTAURÉES par ``put`` direct (mêmes octets, même AST — AUCUN
       re-parse). Sans cela, chaque nom modifié serait parsé deux fois :
       une fois à l'étape 1, une fois au prochain accès (miss sur une
       entrée que la cascade vient de supprimer) — l'exact contraire du
       but de la mission (« ne re-parser que ce qui a changé »).

    Retourne ``{nom: ast}`` pour tout ce qui a été (re-)parsé ou validé
    pendant cet appel (noms modifiés + dépendants eager). ``sources=None``
    (défaut) = mode paresseux : seuls les noms modifiés sont retournés.

    ``sources`` partiel (un dépendant invalidé sans entrée) lève
    ``OctetsSourceManquants`` : échec bruyant plutôt qu'un eager
    silencieusement incomplet.
    """
    if not isinstance(modifications, dict):
        raise FormatAstInattendu(
            "modifications doit être un dict {nom: octets_src}, reçu : %r"
            % (modifications,))
    if sources is not None and not isinstance(sources, dict):
        raise FormatAstInattendu(
            "sources doit être un dict {nom: octets_src} ou None, reçu : %r"
            % (sources,))
    frais = {}
    # -- 1. noms modifiés : D2 décide hit / re-parse -------------------------
    for nom, octets in modifications.items():
        ast = _valider_ou_reparser_d2(nom, octets, parse_fn, cache)
        refs = set()
        for expr in _exprs_de_ast(ast):
            _collecter(expr, refs)
        graphe[nom] = refs
        frais[nom] = ast
    # -- 2. cascade d'invalidation (graphe à jour) ---------------------------
    invalides = invalider_cascade(set(modifications), graphe, cache)
    dependants = sorted(invalides - set(modifications))
    # -- 3. dépendants : eager si on a les octets, sinon paresseux -----------
    if sources is not None:
        for nom in dependants:
            octets = sources.get(nom)
            if octets is None:
                raise OctetsSourceManquants(
                    "re-parse EAGER impossible pour %r : octets sources "
                    "absents de `sources` (et non conservés par ParseCache "
                    "— voir LIMITES HONNÊTES §3)" % (nom,))
            ast = _valider_ou_reparser_d2(nom, octets, parse_fn, cache)
            refs = set()
            for expr in _exprs_de_ast(ast):
                _collecter(expr, refs)
            graphe[nom] = refs
            frais[nom] = ast
    # -- 4. restaure les entrées fraîches des modifiés (DÉCISION D3) ---------
    # La cascade (invalidation D2 par nom, tous hashs) a supprimé les
    # entrées que l'étape 1 venait d'écrire. On les restaure par `put`
    # direct — mêmes octets, mêmes AST, AUCUN re-parse — sinon chaque nom
    # modifié serait parsé deux fois (étape 1 + prochain accès en miss).
    for nom, octets in modifications.items():
        cache.put(nom, calculer_hash_src(octets), frais[nom])
    return frais
