"""
langs/treesitter_generic.py — Analyseur universel piloté par Tree-sitter.

Fonctionne pour n'importe lequel des ~165 langages exposés par
`tree-sitter-language-pack`, SANS table de configuration par langage :
la classification (fonction / boucle / classe / import / commentaire)
repose sur des heuristiques de nommage de nœuds, quasi-universelles dans
l'écosystème des grammaires Tree-sitter (ex: `function_declaration`,
`for_statement`, `class_definition`, `import_statement`, `comment`, ...).

Dépendance optionnelle : `pip install phi-complexity[multilang]`
"""
from typing import List, Optional

from ..core import fibonacci_plus_proche, distance_fibonacci
from ..modeles import MetriqueFonction, Annotation, ResultatAnalyse
from .base import AnalyseurBase

# ────────────────────────────────────────────────────────
# HEURISTIQUES DE CLASSIFICATION DES NŒUDS (universelles)
# ────────────────────────────────────────────────────────

_EXCLUS_FONCTION = ("call", "invocation", "reference", "pointer", "signature", "type")
_SUFFIXES_FONCTION = ("function", "method")
_MARQUEURS_FONCTION = ("declaration", "definition", "expression", "_item")

_BOUCLES = (
    "for_statement", "for_in_statement", "for_range_loop", "foreach_statement",
    "c_style_for_statement", "while_statement", "do_statement",
    "do_while_statement", "repeat_statement", "repeat_while_statement",
    "loop_expression", "for_expression", "while_expression", "until",
)

_CONDITIONS = ("if_statement", "if_expression", "elif_clause")

_CLASSES = (
    "class_declaration", "class_definition", "class_specifier",
    "struct_specifier", "struct_declaration", "struct_item",
    "interface_declaration", "trait_declaration", "object_definition",
    "impl_item", "record_declaration",
    "inductive", "structure", "class",
)

_DECLARATIONS_FONCTION_DIRECTES = (
    "def", "theorem", "lemma", "instance", "axiom",
)

_IMPORTS = (
    "import_statement", "import_declaration", "import_header",
    "use_declaration", "using_directive", "preproc_include",
    "namespace_use_declaration", "require_clause",
    "import",
)

_COMMENTAIRES = ("comment", "line_comment", "block_comment", "multiline_comment", "doc_comment")


def _est_fonction(cible) -> bool:
    """Heuristique universelle : détecte une définition de fonction/méthode/théorème."""
    if hasattr(cible, "type"):
        if not cible.is_named or not cible.named_children:
            return False
        type_noeud = cible.type
    else:
        type_noeud = str(cible)
    t = type_noeud.lower()
    if t in _DECLARATIONS_FONCTION_DIRECTES:
        return True
    if any(mot in t for mot in _EXCLUS_FONCTION):
        return False
    if not any(mot in t for mot in _SUFFIXES_FONCTION):
        return False
    return t.endswith(_SUFFIXES_FONCTION) or any(mot in t for mot in _MARQUEURS_FONCTION)


def _est_boucle(type_noeud: str) -> bool:
    return type_noeud in _BOUCLES


def _est_controle(type_noeud: str) -> bool:
    """Nœuds comptant pour la profondeur d'imbrication (boucles + conditions)."""
    return _est_boucle(type_noeud) or type_noeud in _CONDITIONS


def _est_classe(cible) -> bool:
    if hasattr(cible, "type"):
        if not cible.is_named or not cible.named_children:
            return False
        type_noeud = cible.type
    else:
        type_noeud = str(cible)
    return type_noeud in _CLASSES


def _est_import(type_noeud: str) -> bool:
    return type_noeud in _IMPORTS


def _est_commentaire(type_noeud: str) -> bool:
    return type_noeud in _COMMENTAIRES


# ────────────────────────────────────────────────────────
# ANALYSEUR GÉNÉRIQUE
# ────────────────────────────────────────────────────────

class AnalyseurTreeSitter(AnalyseurBase):
    """
    Analyseur fractal universel basé sur Tree-sitter.
    Dissèque n'importe quel langage supporté par `tree_sitter_language_pack`.
    """

    def __init__(self, fichier: str, ts_langage: str):
        super().__init__(fichier)
        self.ts_langage = ts_langage
        self.tree = None
        self.source: bytes = b""
        self.lignes: List[str] = []
        self.resultat = ResultatAnalyse(fichier=fichier, langage=ts_langage)

    def charger(self) -> "AnalyseurTreeSitter":
        """Charge et parse le fichier source via Tree-sitter."""
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError as exc:
            raise ImportError(
                "Le support multi-langage nécessite 'tree-sitter-language-pack'. "
                "Installez-le via : pip install phi-complexity[multilang]"
            ) from exc

        with open(self.fichier, "r", encoding="utf-8", errors="replace") as f:
            contenu = f.read()
        self.lignes = contenu.splitlines()
        self.source = contenu.encode("utf-8", errors="replace")
        parseur = get_parser(self.ts_langage)
        self.tree = parseur.parse(self.source)
        return self

    def analyser(self, complet: bool = True) -> ResultatAnalyse:
        """
        Lance l'analyse. Orchestre — ne calcule pas directement.

        `complet=True` : analyse complète historique. `complet=False`
        (phase 1) : une seule passe — noms, lignes, classification,
        `complexite = nb_lignes` (PROXY explicite, pas le comptage de
        nœuds du mode complet) ; ni profondeur d'imbrication, ni règles
        souveraines, ni φ-ratios (sentinelles : `profondeur_max=0`,
        `distance_fib=0.0`, `phi_ratio=1.0`).
        """
        if self.tree is None:
            self.charger()
        racine = self.tree.root_node
        self.resultat.nb_lignes_total = len(self.lignes)
        # ────────────────────────────────────────────────────────
        # VERROUILLAGE « zéro tree-sitter silencieux » (2026-10-03) :
        # un arbre avec nœuds ERROR signifie récupération d'erreur —
        # des symboles peuvent manquer SANS aucun signal sinon.
        # On l'annonce en annotation CRITICAL explicite (jamais de
        # silence) ; on ne tente pas de réparer la grammaire externe.
        # ────────────────────────────────────────────────────────
        try:
            if racine.has_error:
                self._annoter(
                    1,
                    "⚠️ TREE-SITTER DÉGRADÉ : l'arbre syntaxique contient "
                    "des nœuds d'erreur — des symboles peuvent manquer "
                    "silencieusement dans cette analyse.",
                    "CRITICAL",
                    "SOUVERAINETE",
                )
        except Exception:
            pass  # la garde ne doit jamais casser l'analyse
        if complet:
            self._compter_elements_globaux(racine)
            self._analyser_fonctions(racine)
            self._appliquer_regles_souveraines(racine)
            self._identifier_oudjat()
        else:
            self._indexer_symboles(racine)
        return self.resultat

    def _indexer_symboles(self, racine):
        """
        Phase 1 (v0.8.0) : une seule passe sur l'arbre — symboles + macro.

        `complexite` = `nb_lignes` (PROXY explicite). Les règles
        souveraines et les φ-ratios sont sautés ; l'oudjat est le
        symbole le plus long (proxy).
        """
        for node in self._parcourir(racine):
            if _est_fonction(node):
                ligne = node.start_point[0] + 1
                nb_lignes = node.end_point[0] + 1 - ligne + 1
                self.resultat.fonctions.append(
                    MetriqueFonction(
                        nom=self._extraire_nom(node),
                        ligne=ligne,
                        complexite=nb_lignes,  # PROXY phase 1 (voir docstring)
                        nb_args=self._compter_args(node),
                        nb_lignes=nb_lignes,
                        profondeur_max=0,   # sentinelle : non calculée
                        distance_fib=0.0,   # sentinelle : non calculée
                        phi_ratio=1.0,      # sentinelle : non calculé
                    )
                )
            elif _est_classe(node):
                self.resultat.nb_classes += 1
            elif _est_import(node.type):
                self.resultat.nb_imports += 1
            elif _est_commentaire(node.type):
                self.resultat.nb_commentaires += 1
        if self.resultat.fonctions:
            self.resultat.oudjat = max(
                self.resultat.fonctions, key=lambda f: f.complexite
            )

    # ────────────────────────────────────────────────────────
    # UTILITAIRES DE PARCOURS
    # ────────────────────────────────────────────────────────

    def _texte(self, node) -> str:
        return self.source[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def _parcourir(self, node):
        """Parcours en profondeur de tous les descendants (nœud inclus)."""
        pile = [node]
        while pile:
            n = pile.pop()
            yield n
            pile.extend(reversed(n.children))

    # ────────────────────────────────────────────────────────
    # COMPTAGE DES ÉLÉMENTS GLOBAUX
    # ────────────────────────────────────────────────────────

    def _compter_elements_globaux(self, racine):
        """Compte classes, imports et commentaires (éléments macro)."""
        for node in self._parcourir(racine):
            if _est_classe(node):
                self.resultat.nb_classes += 1
            elif _est_import(node.type):
                self.resultat.nb_imports += 1
            elif _est_commentaire(node.type):
                self.resultat.nb_commentaires += 1

    # ────────────────────────────────────────────────────────
    # ANALYSE DES FONCTIONS
    # ────────────────────────────────────────────────────────

    def _analyser_fonctions(self, racine):
        """Extrait les métriques de chaque fonction/méthode définie dans le fichier."""
        for node in self._parcourir(racine):
            if _est_fonction(node):
                self.resultat.fonctions.append(self._mesurer_fonction(node))

    def _mesurer_fonction(self, node) -> MetriqueFonction:
        """Calcule toutes les métriques d'une seule fonction."""
        ligne = node.start_point[0] + 1
        fin_ligne = node.end_point[0] + 1
        nb_lignes = fin_ligne - ligne + 1
        return MetriqueFonction(
            nom=self._extraire_nom(node),
            ligne=ligne,
            complexite=sum(1 for _ in self._parcourir(node)),
            nb_args=self._compter_args(node),
            nb_lignes=nb_lignes,
            profondeur_max=self._profondeur_imbrication(node),
            distance_fib=distance_fibonacci(nb_lignes),
            phi_ratio=1.0,  # Calculé après, quand la moyenne est connue
        )

    def _extraire_nom(self, node) -> str:
        """Extrait le nom d'une fonction via le champ 'name' (convention Tree-sitter)."""
        champ = node.child_by_field_name("name")
        if champ is not None:
            return self._texte(champ)
        for c in node.children:
            if c.type in ("identifier", "property_identifier", "field_identifier",
                          "type_identifier", "constant", "simple_identifier"):
                return self._texte(c)
        return "<anonyme>"

    def _compter_args(self, node) -> int:
        """Compte les paramètres d'une fonction via le champ 'parameters' ou liants."""
        params = self._trouver_params(node)
        if params is None:
            return 0
        return sum(1 for c in params.named_children if "comment" not in c.type)

    def _trouver_params(self, node):
        """Recherche le nœud de paramètres, y compris via un déclarateur imbriqué ou liants fonctionnels."""
        champ = node.child_by_field_name("parameters")
        if champ is not None:
            return champ
        # Liants directs pour langages fonctionnels et assistants de preuve (Lean, Agda, etc.)
        for c in node.children:
            if c.type in ("binders", "parameters", "parameter_list"):
                return c
        declarateur = node.child_by_field_name("declarator")
        profondeur = 0
        while declarateur is not None and profondeur < 5:
            champ = declarateur.child_by_field_name("parameters")
            if champ is not None:
                return champ
            declarateur = declarateur.child_by_field_name("declarator")
            profondeur += 1
        return None

    def _identifier_oudjat(self):
        """Identifie la fonction dominante et calcule les φ-ratios."""
        if not self.resultat.fonctions:
            return
        self.resultat.oudjat = max(
            self.resultat.fonctions, key=lambda f: f.complexite
        )
        self._calculer_phi_ratios()

    def _calculer_phi_ratios(self):
        """Normalise la complexité de chaque fonction par la moyenne."""
        moyenne = sum(f.complexite for f in self.resultat.fonctions) / len(
            self.resultat.fonctions
        )
        if moyenne == 0:
            return
        for f in self.resultat.fonctions:
            f.phi_ratio = f.complexite / moyenne

    # ────────────────────────────────────────────────────────
    # RÈGLES DE CODAGE SOUVERAIN (universelles)
    # ────────────────────────────────────────────────────────

    def _appliquer_regles_souveraines(self, racine):
        """Applique les règles souveraines applicables à tous les langages."""
        for node in self._parcourir(racine):
            self._regle_lilith(node)
            self._regle_fibonacci(node)
            self._regle_hermeticite(node)

    def _regle_lilith(self, node):
        """Règle I — Nœuds d'Entropie : détecte les boucles trop imbriquées."""
        if not _est_boucle(node.type):
            return
        depth = self._profondeur_ancetres(node)
        if depth >= 2:
            self._annoter(
                node.start_point[0] + 1,
                f"LILITH : Boucle imbriquée (profondeur {depth}). "
                "La variance s'accumule — envisagez une fonction auxiliaire.",
                "CRITICAL" if depth >= 3 else "WARNING",
                "LILITH"
            )

    def _regle_fibonacci(self, node):
        """Règle III — Taille Naturelle : les fonctions suivent Fibonacci."""
        if not _est_fonction(node):
            return
        ligne = node.start_point[0] + 1
        fin_ligne = node.end_point[0] + 1
        nb_lignes = fin_ligne - ligne + 1
        fib_proche = fibonacci_plus_proche(nb_lignes)
        if nb_lignes > 55 and abs(nb_lignes - fib_proche) > 10:
            nom = self._extraire_nom(node)
            self._annoter(
                ligne,
                f"FIBONACCI : '{nom}' ({nb_lignes} lignes) s'éloigne "
                f"de la séquence naturelle (idéal: {fib_proche}). "
                "Scinder pour réduire la pression morphique.",
                "WARNING",
                "FIBONACCI"
            )

    def _regle_hermeticite(self, node):
        """Règle IV — Herméticité : une fonction ne reçoit pas plus de 5 args."""
        if not _est_fonction(node):
            return
        nb_args = self._compter_args(node)
        if nb_args > 5:
            nom = self._extraire_nom(node)
            self._annoter(
                node.start_point[0] + 1,
                f"SOUVERAINETÉ : '{nom}' reçoit {nb_args} arguments. "
                "Encapsuler dans un objet (max: 5 / idéal φ: 3).",
                "INFO",
                "SOUVERAINETE"
            )

    # ────────────────────────────────────────────────────────
    # UTILITAIRES (profondeur, annotations)
    # ────────────────────────────────────────────────────────

    def _annoter(self, ligne: int, msg: str, niveau: str, categorie: str):
        """Enregistre une annotation chirurgicale sur une ligne de code."""
        extrait = self.lignes[ligne - 1].strip() if ligne <= len(self.lignes) else ""
        self.resultat.annotations.append(
            Annotation(ligne=ligne, message=msg, niveau=niveau,
                       extrait=extrait, categorie=categorie)
        )

    def _profondeur_imbrication(self, fn_node) -> int:
        """Profondeur max d'imbrication à l'intérieur d'une fonction (pile explicite)."""
        pile = [(fn_node, 0)]
        max_depth = 0
        while pile:
            noeud, depth = pile.pop()
            est_ctrl = _est_controle(noeud.type)
            profondeur_courante = depth + 1 if est_ctrl else depth
            max_depth = max(max_depth, profondeur_courante)
            for enfant in noeud.children:
                pile.append((enfant, profondeur_courante))
        return max_depth

    def _profondeur_ancetres(self, node) -> int:
        """Profondeur globale d'un nœud via remontée des parents (natif Tree-sitter)."""
        _COMPTABLE = _CONDITIONS
        compte = 0
        curr = node.parent
        while curr is not None:
            if _est_boucle(curr.type) or curr.type in _COMPTABLE or _est_fonction(curr.type):
                compte += 1
            curr = curr.parent
        return compte
