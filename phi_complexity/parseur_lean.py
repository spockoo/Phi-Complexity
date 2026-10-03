"""Parseur Lean — Vrai AST via tree-sitter.

Franchit la limite "pas de parseur AST réel" : au lieu de manipuler
des chaînes, Phi navigue désormais dans l'arbre syntaxique authentique
du code Lean 4 (grammaire tree-sitter-lean).

Capacités :
- Extraire les déclarations (def / theorem / lemma / abbrev / instance)
  avec nom, binders typés, type de retour, présence d'une preuve.
- Détecter les schémas de quantification dans les types :
  `∀ x, P x` (global) vs `∀ x ∈ s, P x` / `∀ x, x ∈ s → P x` (restreint).
- Naviguer l'AST brut pour des analyses ad hoc.

Limites honnêtes :
- Syntaxique, pas sémantique : le parseur voit la forme, pas le sens
  dans la théorie des types dépendants (pas de résolution de noms,
  pas d'élaboration, pas de vérification de types).
- La grammaire tree-sitter peut diverger de Lean 4 sur des syntaxes
  exotiques (détecté via `has_error`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional


# ---------------------------------------------------------------------------
# Accès paresseux au parseur (le venv phi fournit tree-sitter)
# ---------------------------------------------------------------------------

_parser = None


def _get_parser():
    global _parser
    if _parser is None:
        from tree_sitter_language_pack import get_parser

        _parser = get_parser("lean")
    return _parser


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------


@dataclass
class Binder:
    """Un paramètre typé : (nom : type) ou {nom : type} ou [instance]."""
    nom: str
    type: str
    implicite: bool = False  # True pour { } et [ ]


@dataclass
class Declaration:
    """Une déclaration Lean de premier niveau."""
    kind: str  # 'def', 'theorem', 'lemma', 'abbrev', 'instance', 'example', ...
    nom: str
    binders: List[Binder] = field(default_factory=list)
    type_retour: str = ""
    corps: str = ""  # texte après ':=' (définition ou preuve)
    a_preuve: bool = False
    texte: str = ""
    ligne: int = 0
    # Provenance de l'extraction (durcissement « zéro tree-sitter silencieux »,
    # 2026-10-03) : "analyseur" (tree-sitter) ou "robuste_repli" (regex, quand
    # tree-sitter a avalé la déclaration — p.ex. `|expr|` en position de type).
    extraction: str = "analyseur"
    # Champs du parseur autonome (2026-10-03, autonomie stricte) :
    # "autonome" quand la déclaration vient de parseur_autonome.
    ligne_fin: int = 0
    nb_tactiques: int = 0
    profondeur_by: int = 0


@dataclass
class SchemaQuantification:
    """Un schéma de quantification détecté dans un type."""
    variable: str
    restriction: Optional[str]  # None = global (∀ x, ...), sinon l'ensemble/prédicat
    corps: str

    @property
    def est_global(self) -> bool:
        return self.restriction is None

    @property
    def est_restreint(self) -> bool:
        return self.restriction is not None


# ---------------------------------------------------------------------------
# Navigation AST
# ---------------------------------------------------------------------------


class Noeud:
    """Enveloppe légère autour d'un nœud tree-sitter."""

    def __init__(self, node, code: bytes):
        self._n = node
        self._code = code

    @property
    def type(self) -> str:
        return self._n.type

    @property
    def texte(self) -> str:
        return self._code[self._n.start_byte : self._n.end_byte].decode(
            "utf8", errors="replace"
        )

    @property
    def ligne(self) -> int:
        return self._n.start_point[0] + 1

    @property
    def has_error(self) -> bool:
        return self._n.has_error

    def enfants(self) -> Iterator["Noeud"]:
        for c in self._n.children:
            yield Noeud(c, self._code)

    def enfants_de_type(self, t: str) -> Iterator["Noeud"]:
        for c in self.enfants():
            if c.type == t:
                yield c

    def premier(self, t: str) -> Optional["Noeud"]:
        for c in self.enfants_de_type(t):
            return c
        return None

    def trouver_tous(self, t: str) -> Iterator["Noeud"]:
        """Recherche récursive de tous les nœuds d'un type."""
        if self.type == t:
            yield self
        for c in self.enfants():
            yield from c.trouver_tous(t)


def parse(code: str) -> Noeud:
    """Parse du code Lean et retourne la racine (nœud `module`)."""
    parser = _get_parser()
    code_b = code.encode("utf8") if isinstance(code, str) else code
    tree = parser.parse(code_b)
    return Noeud(tree.root_node, code_b)


# ---------------------------------------------------------------------------
# Extraction des déclarations
# ---------------------------------------------------------------------------

_KINDS = ("def", "theorem", "lemma", "abbrev", "instance", "example", "opaque")


def _parse_binders(noeud_binders: Noeud) -> List[Binder]:
    binders: List[Binder] = []
    for b in noeud_binders.enfants():
        if b.type not in ("explicit_binder", "implicit_binder", "instance_binder"):
            continue
        texte = b.texte.strip()
        implicite = b.type in ("implicit_binder", "instance_binder")
        # Forme typique : (nom : type) — on extrait nom et type
        m = re.match(r"[\(\{\[](.+?)[\)\}\]]", texte, re.DOTALL)
        interieur = m.group(1) if m else texte
        if ":" in interieur:
            nom_part, type_part = interieur.split(":", 1)
            noms = [n.strip() for n in nom_part.split() if n.strip()]
            typ = type_part.strip()
            for n in noms:
                binders.append(Binder(nom=n, type=typ, implicite=implicite))
        else:
            # Binder non typé explicite, ex: (K)
            for n in interieur.split():
                binders.append(Binder(nom=n.strip(), type="_", implicite=implicite))
    return binders


def extraire_declarations(code: str) -> List[Declaration]:
    """Extrait toutes les déclarations de premier niveau du code."""
    racine = parse(code)
    decls: List[Declaration] = []
    for decl in racine.trouver_tous("declaration"):
        # Le premier enfant détermine le kind (nœud `def`, `theorem`, ...)
        kind = "inconnu"
        nom = ""
        binders: List[Binder] = []
        type_retour = ""
        a_preuve = False
        for enfant in decl.enfants():
            if enfant.type in _KINDS and kind == "inconnu":
                kind = enfant.type
                # À l'intérieur : mot-clé, identifier, binders, :, type, :=
                # Le type de retour = nœuds entre ':' et ':='
                # Le corps = nœuds après ':='
                vu_deux_points = False
                vu_defeq = False
                morceaux_retour: List[str] = []
                morceaux_corps: List[str] = []
                for sub in enfant.enfants():
                    if sub.type == "identifier" and not nom:
                        nom = sub.texte
                    elif sub.type == "binders":
                        binders = _parse_binders(sub)
                    elif sub.type == ":=" and not vu_defeq:
                        vu_defeq = True
                        a_preuve = True
                        vu_deux_points = True  # stoppe la capture du retour
                    elif sub.type == ":" and not vu_deux_points and not vu_defeq:
                        vu_deux_points = True
                    elif vu_defeq:
                        morceaux_corps.append(sub.texte)
                    elif vu_deux_points:
                        morceaux_retour.append(sub.texte)
                type_retour = " ".join(" ".join(morceaux_retour).split())
                corps = " ".join(" ".join(morceaux_corps).split())
        if kind != "inconnu":
            decls.append(
                Declaration(
                    kind=kind,
                    nom=nom,
                    binders=binders,
                    type_retour=type_retour,
                    corps=corps,
                    a_preuve=a_preuve,
                    texte=decl.texte,
                    ligne=decl.ligne,
                )
            )
    return decls


# ---------------------------------------------------------------------------
# Détection des schémas de quantification (global vs restreint)
# ---------------------------------------------------------------------------

# ∀ x, P  ou  forall x, P  → global
# ∀ x ∈ s, P  ou  ∀ x, x ∈ s → P  → restreint à s
_PATTERNS_Q = [
    # ∀ x ∈ s, corps  /  ∀ (x : T) ∈ s, corps
    (re.compile(r"∀\s*(\(?\w+[^,]*?\)?)\s*∈\s*(\S+?)\s*,\s*(.+)"), True),
    (re.compile(r"forall\s*(\(?\w+[^,]*?\)?)\s*,\s*\1\s*∈\s*(\S+)\s*->\s*(.+)"), True),
    # ∀ x, corps  /  forall x, corps  → global
    (re.compile(r"∀\s*([^,∈]+?)\s*,\s*(.+)"), False),
    (re.compile(r"forall\s*([^,]+?)\s*,\s*(.+)"), False),
]


def detecter_quantifications(type_str: str) -> List[SchemaQuantification]:
    """
    Détecte TOUS les schémas de quantification dans un type.

    Parcourt tout le texte (pas seulement la tête) pour trouver chaque
    `∀ x, ...` / `∀ x ∈ s, ...` / `forall x, ...`. C'est une analyse
    syntaxique de surface : elle ne traverse pas les notations complexes.
    """
    resultats: List[SchemaQuantification] = []
    # Motif restreint : ∀ <var> ∈ <ens> ,
    pat_restreint = re.compile(r"∀\s*([^,∈]+?)\s*∈\s*([^,]+?)\s*,")
    # Motif global : ∀ <vars> ,  (sans ∈ avant la virgule)
    pat_global = re.compile(r"∀\s*([^,∈]+?)\s*,")
    for m in pat_restreint.finditer(type_str):
        var = " ".join(m.group(1).split())
        ens = " ".join(m.group(2).split())
        resultats.append(SchemaQuantification(variable=var, restriction=ens, corps=""))
    # Globaux : ceux qui ne sont pas déjà capturés comme restreints
    positions_restreintes = {m.start() for m in pat_restreint.finditer(type_str)}
    for m in pat_global.finditer(type_str):
        if m.start() in positions_restreintes:
            continue
        var = " ".join(m.group(1).split())
        resultats.append(SchemaQuantification(variable=var, restriction=None, corps=""))
    # Variante 'forall' ASCII
    pat_forall_r = re.compile(r"forall\s*([^,]+?)\s*,\s*\1\s*∈\s*([^,]+?)\s*->")
    for m in pat_forall_r.finditer(type_str):
        var = " ".join(m.group(1).split())
        ens = " ".join(m.group(2).split())
        resultats.append(SchemaQuantification(variable=var, restriction=ens, corps=""))
    return resultats


def resume_global_vs_restreint(type_str: str) -> str:
    """Résumé lisible : 'global' si aucun quantificateur restreint en tête."""
    qs = detecter_quantifications(type_str)
    if not qs:
        return "aucune quantification détectée"
    details = []
    for q in qs:
        if q.est_global:
            details.append(f"∀ {q.variable} (global)")
        else:
            details.append(f"∀ {q.variable} ∈ {q.restriction} (restreint)")
    return " ; ".join(details)


# ---------------------------------------------------------------------------
# Test intégré
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    exemple = """
def EnergyIdentityHypothesis (K : Nat) (nu : Real) : Prop :=
  True

theorem galerkinEnergy_antitone (K : Nat) (nu : Real) (h : 0 ≤ nu) : True := by
  trivial
"""
    for d in extraire_declarations(exemple):
        print(f"{d.kind} {d.nom} (ligne {d.ligne})")
        for b in d.binders:
            print(f"    binder: {b.nom} : {b.type} (implicite={b.implicite})")
        print(f"    retour: {d.type_retour!r}  preuve: {d.a_preuve}")

    print()
    for t in [
        "∀ s, HasDerivAt α f s → P s",
        "∀ t ∈ s, HasDerivAt α f t → P t",
        "forall x, x ∈ s -> Q x",
    ]:
        print(f"  {t!r}\n    → {resume_global_vs_restreint(t)}")


# ---------------------------------------------------------------------------
# Extraction robuste (sans limite de taille)
# ---------------------------------------------------------------------------

import re as _re

# Regex pour trouver les déclarations (fonctionne sur fichiers de toute taille)
# Mots-clés alignés sur ceux que l'analyseur Lean compte
# (langs/lean.py : theorem/def/abbrev/instance/structure/inductive,
# avec lemma→theorem et class→structure normalisés par la grammaire).
# `example` est extrait aussi (utile hors analyseur, ex. dépendances)
# mais N'EST PAS repris par le repli de l'analyseur.
_DECL_RE = _re.compile(
    r'^(?:private\s+)?(?:protected\s+)?(?:noncomputable\s+)?'
    r'(def|theorem|lemma|abbrev|instance|example|structure|class|inductive)\s+'
    r'([A-Za-z_][A-Za-z0-9_\'\.]*|\([^\)]+\))',
    _re.MULTILINE
)


def extraire_declarations_robuste(code: str) -> List["Declaration"]:
    """Extrait les déclarations par regex (pas de limite de taille).

    Contrairement à extraire_declarations() qui utilise tree-sitter
    (échoue sur fichiers >5000 lignes), cette version utilise une
    regex simple qui fonctionne sur fichiers de toute taille.

    Retourne une liste de Declaration avec nom et ligne.
    Les champs détaillés (binders, type) sont laissés vides ;
    utiliser tree-sitter sur le corps extrait pour l'analyse fine.

    Lacunes documentées (2026-10-02, diagnostic `|·|`):
    - `opaque` n'est pas extrait (l'analyseur Lean ne le compte pas
      non plus : périmètre identique, pas de divergence artificielle) ;
    - un mot-clé dans un commentaire de bloc `/- ... -/` (~1 % des cas
      mesurés sur 176 fichiers) produit un faux positif — le repli de
      l'analyseur l'accepte comme symbole réel : c'est un coût connu,
      explicite, pas un silence ;
    - les déclarations indentées (dans `section`/`namespace`) ne sont
      pas vues — cohérent avec l'analyseur tree-sitter qui ne balaie
      que les enfants directs de la racine.
    """
    from .parseur_lean import Declaration  # import local pour éviter cycle
    declarations = []
    for m in _DECL_RE.finditer(code):
        kind, nom = m.group(1), m.group(2)
        # Calculer la ligne (1-indexed)
        ligne = code[:m.start()].count('\n') + 1
        # Nettoyer le nom (enlever les parenthèses pour les instances anonymes)
        nom = nom.strip()
        if nom.startswith('('):
            continue  # instance anonyme, on saute
        # Un polymorphisme d'univers `foo.{u}` fait capturer le point
        # traînant (`foo.`) : un identifiant Lean ne finit jamais par `.`.
        nom = nom.rstrip('.')
        if not nom:
            continue
        decl = Declaration(
            nom=nom,
            kind=kind,
            ligne=ligne,
            a_preuve=False,  # non déterminé par regex
        )
        declarations.append(decl)
    return declarations


def extraire_corps(code: str, ligne_debut: int, ligne_fin: int = None) -> str:
    """Extrait le corps d'une déclaration par numéros de ligne.

    Args:
        code: le code source complet.
        ligne_debut: ligne de début (1-indexed, inclusive).
        ligne_fin: ligne de fin (1-indexed, exclusive). Si None,
            va jusqu'à la fin du fichier.
    """
    lignes = code.split('\n')
    debut = max(0, ligne_debut - 1)
    fin = ligne_fin - 1 if ligne_fin else len(lignes)
    return '\n'.join(lignes[debut:fin])


def rechercher_declaration(code: str, nom: str) -> "Optional[Declaration]":
    """Recherche une déclaration par nom — source unique : le parseur
    autonome (2026-10-03, autonomie stricte ; remplace le couple
    tree-sitter + repli robuste).

    Retourne None uniquement si la déclaration n'existe vraiment pas —
    c'est alors une vraie absence, pas un aveuglement.
    """
    from .parseur_autonome import rechercher_declaration_autonome
    return rechercher_declaration_autonome(code, nom)


def comparer_extracteurs(code: str, tolerance_ligne: int = 2) -> dict:
    """Compare le parseur autonome (source unique) à tree-sitter
    (vérification croisée optionnelle).

    Contexte historique (2026-10-02) : la grammaire tree-sitter-lean
    confond les barres `|expr|` (valeur absolue/norme) en position de
    type de retour avec une alternative de filtrage `|` ; le nœud ERROR
    produit avale ensuite les déclarations suivantes, qui deviennent
    invisibles à `extraire_declarations` — silencieusement. Depuis
    2026-10-03, le parseur autonome (stdlib uniquement) est la source
    de vérité ; tree-sitter n'est plus sur le chemin critique.

    Retourne {"autonome_seuls": [...], "tree_sitter_seuls": [...]} :
    listes de Declaration vus par un seul des deux extracteurs.
    Deux déclarations sont appariées si même nom et lignes à
    `tolerance_ligne` près.
    """
    from .parseur_autonome import parse_declarations, vers_declaration
    decls_auto = [vers_declaration(d) for d in parse_declarations(code)]
    decls_ts = list(extraire_declarations(code))

    def couvre(candidat, references):
        return any(
            r.nom == candidat.nom and abs(r.ligne - candidat.ligne) <= tolerance_ligne
            for r in references
        )

    return {
        "autonome_seuls": [d for d in decls_auto if not couvre(d, decls_ts)],
        "tree_sitter_seuls": [d for d in decls_ts if not couvre(d, decls_auto)],
    }
