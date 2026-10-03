"""
langs/lean.py — Analyseur Lean 4 dédié (v0.7.0 « Le Lecteur Lean »).

Constat qui motive ce module : l'analyseur Tree-sitter générique extrait
2781 symboles du projet Lean mais produit 292 collisions dont la
quasi-totalité est du BRUIT — `<anonyme>` ×16 et des lieurs locaux
(`omega`, `xi`, `term_id`…) comptés comme symboles de tête. Un index qui
confond un lieur avec un théorème n'est pas un instrument.

Règle d'extraction : seuls les nœuds `declaration` de premier niveau dont
le mot-clé est theorem/lemma/def/abbrev/instance/structure/class/inductive,
avec un nom = premier `identifier` avant `:`, `:=` ou `where`.
Sont IGNORÉS : les lieurs, les identifiants internes, les définitions
anonymes (`_…`) et les instances anonymes (sans nom).

Grammaire : `tree_sitter_language_pack.get_language('lean')`
(téléchargement à la demande, cache `~/.cache/tree-sitter-language-pack/`).
Dépendance optionnelle : sans le pack ou sans la grammaire, `charger()`
lève une ImportError explicite « grammaire lean indisponible » — jamais
de repli silencieux vers le générique (qui reproduirait le bruit mesuré).

Proxy de complexité (EXPLICITE) : `complexite = nb_lignes + nb_tactiques`,
où nb_tactiques = nombre de pas de tactique (enfants nommés directs des
blocs `by` de la déclaration). Justification : en Lean la difficulté d'une
déclaration se lit dans la longueur de son texte et le nombre de ses pas
de preuve, pas dans le nombre brut de nœuds syntaxiques — qui gonfle
artificiellement avec les types, les lieurs et les applications.

Limites connues et assumées :
- `import A.B` est mal parsé par la grammaire (nœud ERROR) : nb_imports
  reste à 0, documenté plutôt que faux.
- `lemma` et `class` sont normalisés par la grammaire en nœuds `theorem`
  et `structure` ; le vrai mot-clé est relu dans le texte pour le libellé.
"""
from typing import List, Optional

from ..core import distance_fibonacci
from ..modeles import MetriqueFonction, Annotation, ResultatAnalyse
from .base import AnalyseurBase

# Types de nœuds mot-clé (tels que la grammaire les normalise).
_TYPES_DECLARATION = {"theorem", "def", "abbrev", "instance", "structure", "inductive"}

# Kinds récupérables par le repli robuste : exactement ceux que
# l'analyseur compte (mots-clés SOURCE, avant normalisation —
# `lemma`→theorem et `class`→structure sont normalisés par la grammaire).
# `example` et `opaque` en sont exclus : le repli répare un angle mort,
# il n'étend pas le périmètre.
_KINDS_COMPTES = {"theorem", "lemma", "def", "abbrev", "instance",
                  "structure", "class", "inductive"}

# Enfants de `declaration` à sauter avant le mot-clé.
_PREFIXES_IGNORES = {"decl_modifiers", "attributes"}

# Jetons anonymes qui terminent la zone du nom dans l'en-tête.
_FIN_NOM = {":", ":=", "where"}

_COMMENTAIRES = {"comment", "line_comment", "block_comment"}

_GRAMMAIRE_CACHE = {}


def grammaire_lean_disponible() -> bool:
    """
    Indique si la grammaire Lean est réellement utilisable.

    Vrai si `tree-sitter-language-pack` est présent ET que
    `get_language('lean')` réussit (téléchargement à la demande, puis
    cache disque `~/.cache/tree-sitter-language-pack/` qui persiste aux
    reboots). Faux sinon — jamais d'exception ici, c'est un test.
    """
    if "ok" in _GRAMMAIRE_CACHE:
        return _GRAMMAIRE_CACHE["ok"]
    try:
        from tree_sitter_language_pack import get_language
        get_language("lean")
        _GRAMMAIRE_CACHE["ok"] = True
    except Exception:
        _GRAMMAIRE_CACHE["ok"] = False
    return _GRAMMAIRE_CACHE["ok"]


class AnalyseurLean(AnalyseurBase):
    """
    Analyseur fractal dédié à Lean 4 : déclarations de tête uniquement,
    métriques calibrées pour le code de preuve.

    Moteur d'extraction (2026-10-03, autonomie stricte) :
    - "autonome" (défaut) : parseur_autonome (stdlib uniquement, zéro
      dépendance externe) — source unique sur le chemin Lean ;
    - "tree_sitter" : ancien chemin tree-sitter, conservé pour vérification
      croisée optionnelle (désactivé par défaut).
    """

    def __init__(self, fichier: str, moteur: str = "autonome"):
        super().__init__(fichier)
        self.moteur = moteur
        self.tree = None
        self.source: bytes = b""
        self.lignes: List[str] = []
        self.resultat = ResultatAnalyse(fichier=fichier, langage="lean")

    def charger(self) -> "AnalyseurLean":
        """Charge le fichier. Le moteur autonome ne nécessite rien
        d'externe ; le moteur tree_sitter lève ImportError explicite si
        la grammaire manque."""
        if self.moteur == "autonome":
            with open(self.fichier, "r", encoding="utf-8", errors="replace") as f:
                contenu = f.read()
            self.lignes = contenu.splitlines()
            self.source = contenu.encode("utf-8", errors="replace")
            self._contenu = contenu
            return self
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError as exc:
            raise ImportError(
                "L'analyse Lean nécessite 'tree-sitter-language-pack' "
                "(pip install phi-complexity[multilang]) : grammaire lean indisponible."
            ) from exc
        try:
            parseur = get_parser("lean")
        except Exception as exc:
            raise ImportError(
                "Grammaire lean indisponible via tree-sitter-language-pack "
                "(téléchargement à la demande impossible) : analyse Lean non supportée."
            ) from exc

        with open(self.fichier, "r", encoding="utf-8", errors="replace") as f:
            contenu = f.read()
        self.lignes = contenu.splitlines()
        self.source = contenu.encode("utf-8", errors="replace")
        self.tree = parseur.parse(self.source)
        return self

    def analyser(self, complet: bool = True) -> ResultatAnalyse:
        """
        Analyse : déclarations de tête, métriques, règles, oudjat.

        Moteur "autonome" (défaut) : parseur proprietaire sans dépendance
        externe. Moteur "tree_sitter" : ancien chemin (vérification croisée).

        `complet=True` : comportement historique. `complet=False`
        (phase 1) : noms, lignes, `complexite = nb_lignes` (PROXY explicite,
        pas le proxy lignes+tactiques du mode complet) ; ni comptage de
        tactiques, ni profondeur `by`, ni règle Fibonacci, ni φ-ratios
        (sentinelles : `profondeur_max=0`, `distance_fib=0.0`,
        `phi_ratio=1.0`). L'oudjat est la déclaration la plus longue.
        """
        if self.moteur == "autonome":
            return self._analyser_autonome(complet=complet)
        if self.tree is None:
            self.charger()
        racine = self.tree.root_node
        self.resultat.nb_lignes_total = len(self.lignes)
        for enfant in racine.children:
            if enfant.type == "declaration":
                mot_cle = self._noeud_mot_cle(enfant)
                mesure = self._mesurer_declaration(enfant, mot_cle, complet=complet)
                if mesure is not None:
                    self.resultat.fonctions.append(mesure)
                    if self._mot_cle_texte(mot_cle) in ("structure", "class", "inductive"):
                        self.resultat.nb_classes += 1
            elif enfant.type in _COMMENTAIRES:
                self.resultat.nb_commentaires += 1
        # Durcissement 2026-10-02 : repli robuste — récupère les
        # déclarations avalées par la récupération d'erreur tree-sitter
        # (cause racine : `|expr|` en position de type, voir _repli_robuste).
        self._repli_robuste()
        if complet:
            self._regle_fibonacci()
        self._identifier_oudjat(complet=complet)
        return self.resultat

    # ────────────────────────────────────────────────────────
    # MOTEUR AUTONOME (2026-10-03, autonomie stricte)
    # ────────────────────────────────────────────────────────

    def _analyser_autonome(self, complet: bool = True) -> ResultatAnalyse:
        """Analyse via le parseur autonome (stdlib uniquement).

        Même contrat de sortie que le chemin tree-sitter : symboles
        comptés = KINDS_COMPTES, complexite = nb_lignes + nb_tactiques
        (mode complet) ou nb_lignes (phase 1, PROXY explicite).
        Les avertissements du parseur deviennent des annotations WARNING
        — jamais silencieux.
        """
        from ..parseur_autonome import parse as parse_autonome, KINDS_COMPTES
        if not hasattr(self, "_contenu"):
            self.charger()
        contenu = self._contenu
        self.resultat.nb_lignes_total = len(self.lignes)
        res = parse_autonome(contenu)
        self.resultat.nb_commentaires = res.nb_commentaires
        for d in res.declarations:
            if d.kind not in KINDS_COMPTES:
                continue
            if d.nom.startswith("_"):
                continue  # définitions anonymes, comme avant
            nb_lignes = d.ligne_fin - d.ligne_debut + 1
            if not complet:
                mesure = MetriqueFonction(
                    nom=d.nom,
                    ligne=d.ligne,
                    complexite=nb_lignes,  # PROXY phase 1 (voir docstring analyser)
                    nb_args=0,             # sentinelle : non calculé
                    nb_lignes=nb_lignes,
                    profondeur_max=0,      # sentinelle : non calculée
                    distance_fib=0.0,      # sentinelle : non calculée
                    phi_ratio=1.0,         # sentinelle : non calculé
                    extraction="autonome",
                )
            else:
                mesure = MetriqueFonction(
                    nom=d.nom,
                    ligne=d.ligne,
                    complexite=nb_lignes + d.nb_tactiques,
                    nb_args=d.nb_args,
                    nb_lignes=nb_lignes,
                    profondeur_max=d.profondeur_by,
                    distance_fib=distance_fibonacci(nb_lignes),
                    phi_ratio=1.0,  # Calculé après, quand la moyenne est connue
                    extraction="autonome",
                )
            self.resultat.fonctions.append(mesure)
            if d.kind in ("structure", "class", "inductive"):
                self.resultat.nb_classes += 1
        # Avertissements du parseur → annotations bruyantes.
        for av in res.avertissements:
            extrait = self.lignes[av.ligne - 1].strip() \
                if 0 < av.ligne <= len(self.lignes) else ""
            self.resultat.annotations.append(
                Annotation(
                    ligne=av.ligne,
                    message=f"PARSEUR AUTONOME : {av.message}",
                    niveau="WARNING",
                    extrait=extrait,
                    categorie="PARSEUR",
                )
            )
        if complet:
            self._regle_fibonacci()
        self._identifier_oudjat(complet=complet)
        return self.resultat

    # ────────────────────────────────────────────────────────
    # EXTRACTION DES DÉCLARATIONS (chemin tree_sitter)
    # ────────────────────────────────────────────────────────

    def _noeud_mot_cle(self, declaration):
        """Retourne le nœud mot-clé (theorem/def/…), en sautant modificateurs et attributs."""
        for enfant in declaration.children:
            if enfant.type in _PREFIXES_IGNORES:
                continue
            if enfant.is_named and enfant.type in _TYPES_DECLARATION:
                return enfant
            # Premier enfant significatif non reconnu : pas une déclaration gérée.
            if enfant.is_named:
                return None
        return None

    def _mot_cle_texte(self, noeud) -> str:
        """Le vrai mot-clé source (la grammaire normalise lemma→theorem, class→structure)."""
        for enfant in noeud.children:
            if not enfant.is_named:
                texte = self._texte(enfant)
                if texte in ("theorem", "lemma", "def", "abbrev", "instance",
                             "structure", "class", "inductive"):
                    return texte
        return noeud.type

    def _extraire_nom(self, noeud) -> Optional[str]:
        """
        Nom = premier `identifier` avant `:`, `:=` ou `where`.

        None pour les instances anonymes ; les noms `_…` sont rejetés par
        l'appelant (définitions anonymes).
        """
        for enfant in noeud.children:
            if not enfant.is_named and self._texte(enfant) in _FIN_NOM:
                break
            if enfant.is_named and enfant.type == "identifier":
                return self._texte(enfant)
        return None

    def _mesurer_declaration(self, declaration, mot_cle,
                             complet: bool = True) -> Optional[MetriqueFonction]:
        """
        Mesure une déclaration, ou None si anonyme / non gérée.

        Phase 1 (`complet=False`) : `complexite = nb_lignes` (PROXY),
        sentinelles `nb_args=0`, `profondeur_max=0`, `distance_fib=0.0`,
        `phi_ratio=1.0` — aucun parcours de sous-arbre.
        """
        if mot_cle is None:
            return None
        nom = self._extraire_nom(mot_cle)
        if nom is None or nom.startswith("_"):
            return None
        ligne = declaration.start_point[0] + 1
        nb_lignes = declaration.end_point[0] - declaration.start_point[0] + 1
        if not complet:
            return MetriqueFonction(
                nom=nom,
                ligne=ligne,
                complexite=nb_lignes,  # PROXY phase 1 (voir docstring analyser)
                nb_args=0,             # sentinelle : non calculé
                nb_lignes=nb_lignes,
                profondeur_max=0,      # sentinelle : non calculée
                distance_fib=0.0,      # sentinelle : non calculée
                phi_ratio=1.0,         # sentinelle : non calculé
            )
        nb_tactiques = self._compter_tactiques(declaration)
        return MetriqueFonction(
            nom=nom,
            ligne=ligne,
            complexite=nb_lignes + nb_tactiques,
            nb_args=self._compter_args(mot_cle),
            nb_lignes=nb_lignes,
            profondeur_max=self._profondeur_by(declaration),
            distance_fib=distance_fibonacci(nb_lignes),
            phi_ratio=1.0,  # Calculé après, quand la moyenne est connue
        )

    # ────────────────────────────────────────────────────────
    # REPLI ROBUSTE (durcissement 2026-10-02)
    # ────────────────────────────────────────────────────────

    def _repli_robuste(self) -> int:
        """Récupère les déclarations avalées par la récupération d'erreur.

        Cause racine : la grammaire tree-sitter-lean confond les barres
        `|expr|` (valeur absolue / norme) en position de type de retour
        avec une alternative de filtrage `|` ; le nœud ERROR produit
        avale ensuite les déclarations suivantes, qui deviennent
        INVISIBLES à l'analyseur — silencieusement (constaté :
        169 déclarations vues sur 261 dans scratch_65c_global.lean,
        412 déclarations manquées sur 60/176 fichiers du dépôt Lean).

        L'extracteur robuste (regex) est la source de vérité pour
        l'EXISTENCE des symboles ; tree-sitter garde la main sur les
        métriques fines quand il voit la déclaration. Les symboles
        récupérés portent extraction="robuste_repli" et des métriques
        PROXY honnêtes (complexite = nb_lignes du span, comme la
        phase 1) — jamais de métriques inventées, jamais de silence.

        Seuls les kinds déjà comptés par l'analyseur sont récupérés
        (pas de `example`, pas de `opaque`) : le repli ne crée aucun
        nouveau périmètre, il répare un angle mort.

        Retourne le nombre de symboles récupérés. Le repli ne lève
        jamais : en cas d'échec il ne fait rien (l'analyse principale
        reste intacte).
        """
        try:
            from ..parseur_lean import extraire_declarations_robuste
            code = self.source.decode("utf-8", errors="replace")
            robustes = sorted(extraire_declarations_robuste(code),
                              key=lambda d: d.ligne)
            # Référence de couverture : ce que la passe tree-sitter a
            # VRAIMENT extrait (self.resultat.fonctions) — et non
            # extraire_declarations(), qui ignore structure/inductive
            # alors que l'analyseur les compte (doublons sinon).
            vus = [(f.nom, f.ligne) for f in self.resultat.fonctions]

            def couvert(d):
                return any(n == d.nom and abs(l - d.ligne) <= 2
                           for (n, l) in vus)

            manquants = [
                d for d in robustes
                if d.kind in _KINDS_COMPTES and not d.nom.startswith("_")
                and not couvert(d)
            ]
            if not manquants:
                return 0
            # Spans : chaque déclaration robuste va jusqu'à la suivante
            # (tous kinds, comme le découpage par grep de _spans_symboles).
            lignes_fin = {}
            for i, d in enumerate(robustes):
                fin = (robustes[i + 1].ligne - 1
                       if i + 1 < len(robustes) else len(self.lignes))
                lignes_fin[(d.nom, d.ligne)] = max(d.ligne, fin)
            recuperes = 0
            for d in manquants:
                fin = lignes_fin.get((d.nom, d.ligne), len(self.lignes))
                span = max(1, fin - d.ligne + 1)
                self.resultat.fonctions.append(MetriqueFonction(
                    nom=d.nom,
                    ligne=d.ligne,
                    complexite=span,   # PROXY phase 1, explicite
                    nb_args=0,         # sentinelle : non calculé
                    nb_lignes=span,
                    profondeur_max=0,  # sentinelle : non calculée
                    distance_fib=distance_fibonacci(span),
                    phi_ratio=1.0,     # sentinelle : non calculé
                    extraction="robuste_repli",
                ))
                if d.kind in ("structure", "class", "inductive"):
                    self.resultat.nb_classes += 1
                recuperes += 1
            return recuperes
        except Exception:
            return 0

    def _compter_tactiques(self, declaration) -> int:
        """Nombre de pas de tactique = enfants nommés directs des blocs `by`."""
        total = 0
        for noeud in self._parcourir(declaration):
            if noeud.type == "by":
                total += sum(
                    1 for c in noeud.children if c.is_named
                )
        return total

    def _profondeur_by(self, declaration) -> int:
        """Imbrication maximale de blocs de tactique `by` (0 = terme direct)."""
        maximum = 0
        pile = [(declaration, 0)]
        while pile:
            noeud, profondeur = pile.pop()
            if noeud.type == "by":
                profondeur += 1
                maximum = max(maximum, profondeur)
            for enfant in noeud.children:
                pile.append((enfant, profondeur))
        return maximum

    def _compter_args(self, mot_cle) -> int:
        """Nombre d'arguments = identifiants liés avant `:` dans les lieurs explicites/implicites."""
        total = 0
        for noeud in self._parcourir(mot_cle):
            if noeud.type in ("explicit_binder", "implicit_binder"):
                for enfant in noeud.children:
                    if not enfant.is_named and self._texte(enfant) == ":":
                        break
                    if enfant.is_named and enfant.type == "identifier":
                        total += 1
        return total

    # ────────────────────────────────────────────────────────
    # UTILITAIRES
    # ────────────────────────────────────────────────────────

    def _texte(self, noeud) -> str:
        return self.source[noeud.start_byte:noeud.end_byte].decode("utf-8", "replace")

    def _parcourir(self, noeud):
        pile = [noeud]
        while pile:
            n = pile.pop()
            yield n
            pile.extend(reversed(n.children))

    def _identifier_oudjat(self, complet: bool = True):
        """Oudjat = déclaration la plus complexe ; φ-ratios en mode complet seul."""
        if not self.resultat.fonctions:
            return
        self.resultat.oudjat = max(
            self.resultat.fonctions, key=lambda f: f.complexite
        )
        if not complet:
            return
        moyenne = sum(f.complexite for f in self.resultat.fonctions) / len(
            self.resultat.fonctions
        )
        if moyenne:
            for f in self.resultat.fonctions:
                f.phi_ratio = f.complexite / moyenne

    def _regle_fibonacci(self):
        """Règle III — Taille Naturelle, adaptée : longueur + pas de tactique."""
        from ..core import fibonacci_plus_proche
        for f in self.resultat.fonctions:
            if f.nb_lignes > 55 and abs(f.nb_lignes - fibonacci_plus_proche(f.nb_lignes)) > 10:
                extrait = self.lignes[f.ligne - 1].strip() if f.ligne <= len(self.lignes) else ""
                self.resultat.annotations.append(
                    Annotation(
                        ligne=f.ligne,
                        message=f"FIBONACCI : '{f.nom}' ({f.nb_lignes} lignes) s'éloigne "
                                f"de la séquence naturelle (idéal: {fibonacci_plus_proche(f.nb_lignes)}). "
                                "Scinder pour réduire la pression morphique.",
                        niveau="WARNING",
                        extrait=extrait,
                        categorie="FIBONACCI",
                    )
                )
