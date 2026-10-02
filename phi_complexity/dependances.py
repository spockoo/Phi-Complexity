"""Extraction des dépendances entre définitions Lean.

Utilise le parseur tree-sitter (parseur_lean.py) pour :
1. Extraire toutes les déclarations d'un fichier.
2. Pour chaque déclaration, trouver les identifiants référencés dans son corps.
3. Construire le graphe de dépendances.

Limites honnêtes :
- Syntaxique, pas sémantique : on extrait les identifiants textuels,
  sans résolution de noms complète (pas de distinction entre
  variable liée et référence globale sans analyse de portée).
- On filtre les mots-clés Lean et les identifiants locaux évidents,
  mais des faux positifs sont possibles.
- Pour une exactitude totale, il faudrait l'élaborateur Lean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from .parseur_lean import parse, extraire_declarations, extraire_declarations_robuste


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
    """Extrait tous les identifiants d'un fragment de code Lean."""
    try:
        racine = parse(code)
    except Exception:
        return set()
    identifiants = set()
    for noeud in racine.trouver_tous("identifier"):
        texte = noeud.texte.strip()
        # Filtrer les mots-clés et les identifiants vides
        if texte and texte not in MOTS_CLES and not texte.startswith("_"):
            # Ignorer les projections de champ simples (ex: x.val -> on garde x)
            # Pour l'instant, on garde le texte brut
            identifiants.add(texte)
    return identifiants


def analyser_fichier(chemin: str) -> GrapheDependances:
    """Analyse un fichier Lean et construit le graphe de dépendances."""
    graphe = GrapheDependances()
    try:
        with open(chemin, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception:
        return graphe

    # Extraire les déclarations (version robuste, pas de limite de taille)
    declarations = extraire_declarations_robuste(code)
    noms_declares = {d.nom for d in declarations if d.nom}

    # Pour chaque déclaration, extraire les identifiants de son corps
    # On utilise une approche simple : découper le code par déclaration
    # (une approche plus précise utiliserait les positions AST)
    for decl in declarations:
        if not decl.nom:
            continue
        # Trouver le corps : on prend le texte après ":=" jusqu'à la prochaine déclaration
        # Simplification : on analyse tout le fichier et on filtre
        # (pour une version précise, il faudrait les offsets AST)
        pass

    # Approche précise : extraire le corps entre deux déclarations
    # On trie les déclarations par ligne, puis pour chacune,
    # le corps va de sa ligne jusqu'à la ligne de la suivante.
    lignes = code.split('\n')
    decls_triees = sorted(
        [d for d in declarations if d.nom and d.ligne],
        key=lambda d: d.ligne
    )
    for idx, decl in enumerate(decls_triees):
        # Début : ligne de la déclaration (0-indexed)
        debut = decl.ligne - 1
        # Fin : ligne de la prochaine déclaration (exclusive),
        # ou fin du fichier si c'est la dernière
        if idx + 1 < len(decls_triees):
            fin = decls_triees[idx + 1].ligne - 1
        else:
            fin = len(lignes)
        # Extraire le bloc exact
        bloc = '\n'.join(lignes[debut:fin])
        # Extraire les identifiants du bloc
        refs = extraire_identifiants(bloc)
        # Retirer l'auto-référence
        refs.discard(decl.nom)
        graphe.ajouter(decl.nom, refs, chemin, decl.ligne)

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
