"""Extraction des dépendances entre définitions Lean.

Utilise le parseur autonome (parseur_autonome.py, 2026-10-03, autonomie
stricte) pour :
1. Extraire toutes les déclarations d’un fichier (spans exacts).
2. Pour chaque déclaration, trouver les identifiants référencés dans son corps.
3. Construire le graphe de dépendances.

Zéro dépendance externe. Limites honnêtes :
- Syntaxique, pas sémantique : on extrait les identifiants textuels,
  sans résolution de noms complète (pas de distinction entre
  variable liée et référence globale sans analyse de portée).
- On filtre les mots-clés Lean et les identifiants locaux évidents,
  mais des faux positifs sont possibles.
- Pour une exactitude totale, il faudrait l'élaborateur Lean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set



# Mots-clés Lean à ignorer (non des dépendances)
MOTS_CLES = {
    "def", "theorem", "lemma", "abbrev", "instance", "example",
    "where", "by", "intro", "exact", "apply", "rw", "simp", "have",
    "let", "fun", "∀", "∃", "→", ":=", ":", "Prop", "Type", "Sort",
    "true", "false", "if", "then", "else", "match", "with",
    "do", "for", "in", "open", "import", "namespace", "end",
    "variable", "section", "set", "show", "calc", "sorry",
}


@dataclass
class GrapheDependances:
    """Graphe des dépendances entre définitions."""
    # nom -> ensemble des noms référencés
    dependances: Dict[str, Set[str]] = field(default_factory=dict)
    # nom -> (fichier, ligne)
    positions: Dict[str, tuple] = field(default_factory=dict)

    def ajouter(self, nom: str, refs: Set[str], fichier: str = "", ligne: int = 0):
        """Ajoute les dépendances d'une définition."""
        if nom not in self.dependances:
            self.dependances[nom] = set()
        self.dependances[nom].update(refs)
        if fichier:
            self.positions[nom] = (fichier, ligne)

    def fermeture(self, nom: str, vus: Set[str] = None) -> Set[str]:
        """Calcule la fermeture transitive des dépendances."""
        if vus is None:
            vus = set()
        if nom in vus:
            return set()
        vus.add(nom)
        resultat = set()
        for dep in self.dependances.get(nom, set()):
            resultat.add(dep)
            resultat.update(self.fermeture(dep, vus))
        return resultat

    def tri_topologique(self, noms: List[str]) -> List[str]:
        """Trie les noms en ordre topologique (dépendances d'abord).
        Lève une exception si cycle détecté."""
        visite = set()
        ordre = []
        temp = set()

        def visiter(n):
            if n in temp:
                raise ValueError(f"Cycle détecté impliquant {n}")
            if n in visite:
                return
            temp.add(n)
            for dep in self.dependances.get(n, set()):
                if dep in self.dependances:  # Seulement si c'est une définition connue
                    visiter(dep)
            temp.remove(n)
            visite.add(n)
            ordre.append(n)

        for n in noms:
            visiter(n)
        return ordre


def extraire_identifiants(code: str) -> Set[str]:
    """Extrait tous les identifiants d'un fragment de code Lean.

    Source unique : le lexer du parseur autonome (2026-10-03). Les
    commentaires et chaînes sont exclus par construction (tokens).
    Ne rend jamais un ensemble vide silencieusement : si le code ne
    contient vraiment aucun identifiant, c'est le cas — sinon le
    lexer les voit tous (aucune grammaire externe).
    """
    from .parseur_autonome import tokenize, identifiants
    toks, _avs, _nc = tokenize(code)
    return {nom for nom, _ligne in identifiants(toks)
            if nom and nom not in MOTS_CLES and not nom.startswith("_")}


_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")


def _identifiants_regex(code: str) -> Set[str]:
    """Repli regex pour `extraire_identifiants` quand tree-sitter est
    indisponible (jamais de silence : ensemble vide seulement si le
    code n'a vraiment aucun identifiant)."""
    return {m.group(0) for m in _RE_IDENT.finditer(code)
            if m.group(0) not in MOTS_CLES
            and not m.group(0).startswith("_")}


def analyser_fichier(chemin: str) -> GrapheDependances:
    """Analyse un fichier Lean et construit le graphe de dépendances.

    Source unique : parseur autonome — déclarations avec spans de corps
    exacts (pas de découpage par lignes approximatif).
    """
    from .parseur_autonome import parse_declarations
    graphe = GrapheDependances()
    try:
        with open(chemin, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception:
        return graphe

    for decl in parse_declarations(code):
        if not decl.nom:
            continue
        refs = extraire_identifiants(decl.corps)
        refs.discard(decl.nom)
        # Retirer les noms liés par les binders (paramètres, pas dépendances)
        for bnom, _impl in decl.binders:
            refs.discard(bnom)
        graphe.ajouter(decl.nom, refs, chemin, decl.ligne)
    return graphe

    return graphe



def dependances_pour(nom: str, graphe: GrapheDependances) -> List[str]:
    """Retourne la liste triée des dépendances transitives d'un nom,
    en ordre topologique (dépendances d'abord)."""
    fermeture = graphe.fermeture(nom)
    # Filtrer pour ne garder que les noms connus du graphe
    noms_connus = [n for n in fermeture if n in graphe.dependances]
    try:
        return graphe.tri_topologique(noms_connus)
    except ValueError:
        # En cas de cycle, retourner trié alphabétiquement
        return sorted(noms_connus)
