"""
phi_complexity/formules/nodes.py — Nœuds de l'arbre de syntaxe du langage de formules.

Dérivé du parseur d'expressions de la calculatrice GNS-754 (Tomy Verreault, 2026),
étendu pour le jugement quantitatif : identifiants de métriques, comparaisons
(>, <, >=, <=, ==, !=), opérateurs logiques (and, or, not) et fonctions à un
argument (sqrt, abs, log, log2, exp).

Sémantique : l'arithmétique rend des flottants, les comparaisons et la logique
rendent des booléens Python. Aucune dépendance hors stdlib.
"""
from abc import ABC, abstractmethod
import math


class NoeudAST(ABC):
    """Classe de base abstraite pour tous les nœuds du langage de formules."""

    @abstractmethod
    def evaluer(self, env: dict):
        """Évalue le nœud dans l'environnement (nom de métrique -> valeur)."""
        ...


class NoeudNombre(NoeudAST):
    """Constante numérique littérale."""

    def __init__(self, valeur: float):
        self.valeur = float(valeur)

    def evaluer(self, env: dict) -> float:
        return self.valeur

    def __repr__(self):
        return f"NoeudNombre({self.valeur})"


class NoeudConstante(NoeudAST):
    """Constante mathématique nommée : phi, pi, e."""

    CONSTANTES = {
        "phi": (1 + math.sqrt(5)) / 2,
        "pi": math.pi,
        "e": math.e,
    }

    def __init__(self, nom: str):
        self.nom = nom.lower()

    def evaluer(self, env: dict) -> float:
        return self.CONSTANTES[self.nom]

    def __repr__(self):
        return f"NoeudConstante('{self.nom}')"


class NoeudIdentifiant(NoeudAST):
    """Référence à une métrique fournie dans l'environnement."""

    def __init__(self, nom: str):
        self.nom = nom

    def evaluer(self, env: dict) -> float:
        if self.nom in env:
            return float(env[self.nom])
        raise NameError(
            f"Métrique inconnue : '{self.nom}'. "
            f"Noms disponibles : {sorted(env)}"
        )

    def __repr__(self):
        return f"NoeudIdentifiant('{self.nom}')"


class NoeudBinaire(NoeudAST):
    """Opération arithmétique binaire : +, -, *, /, ^ (puissance, associatif à droite)."""

    def __init__(self, gauche: NoeudAST, op: str, droite: NoeudAST):
        self.gauche = gauche
        self.op = op
        self.droite = droite

    def evaluer(self, env: dict) -> float:
        g = self.gauche.evaluer(env)
        d = self.droite.evaluer(env)
        if self.op == "+":
            return g + d
        if self.op == "-":
            return g - d
        if self.op == "*":
            return g * d
        if self.op == "/":
            if d == 0:
                raise ZeroDivisionError("Division par zéro dans la formule")
            return g / d
        if self.op == "^":
            try:
                return math.pow(g, d)
            except OverflowError:
                raise OverflowError("Puissance hors limites dans la formule")
        raise ValueError(f"Opérateur arithmétique inconnu : {self.op}")

    def __repr__(self):
        return f"NoeudBinaire({self.gauche}, '{self.op}', {self.droite})"


class NoeudComparaison(NoeudAST):
    """Comparaison : >, <, >=, <=, ==, !=. Rend un booléen."""

    OPERATEURS = {">", "<", ">=", "<=", "==", "!="}

    def __init__(self, gauche: NoeudAST, op: str, droite: NoeudAST):
        if op not in self.OPERATEURS:
            raise ValueError(f"Opérateur de comparaison inconnu : {op}")
        self.gauche = gauche
        self.op = op
        self.droite = droite

    def evaluer(self, env: dict) -> bool:
        g = self.gauche.evaluer(env)
        d = self.droite.evaluer(env)
        if self.op == ">":
            return g > d
        if self.op == "<":
            return g < d
        if self.op == ">=":
            return g >= d
        if self.op == "<=":
            return g <= d
        if self.op == "==":
            return g == d
        return g != d  # !=

    def __repr__(self):
        return f"NoeudComparaison({self.gauche}, '{self.op}', {self.droite})"


class NoeudEt(NoeudAST):
    """Conjonction logique avec court-circuit. Rend un booléen."""

    def __init__(self, gauche: NoeudAST, droite: NoeudAST):
        self.gauche = gauche
        self.droite = droite

    def evaluer(self, env: dict) -> bool:
        return bool(self.gauche.evaluer(env)) and bool(self.droite.evaluer(env))

    def __repr__(self):
        return f"NoeudEt({self.gauche}, {self.droite})"


class NoeudOu(NoeudAST):
    """Disjonction logique avec court-circuit. Rend un booléen."""

    def __init__(self, gauche: NoeudAST, droite: NoeudAST):
        self.gauche = gauche
        self.droite = droite

    def evaluer(self, env: dict) -> bool:
        return bool(self.gauche.evaluer(env)) or bool(self.droite.evaluer(env))

    def __repr__(self):
        return f"NoeudOu({self.gauche}, {self.droite})"


class NoeudNon(NoeudAST):
    """Négation logique. Rend un booléen."""

    def __init__(self, operande: NoeudAST):
        self.operande = operande

    def evaluer(self, env: dict) -> bool:
        return not bool(self.operande.evaluer(env))

    def __repr__(self):
        return f"NoeudNon({self.operande})"


class NoeudUnaire(NoeudAST):
    """Opérateur unaire arithmétique : -x, +x."""

    def __init__(self, op: str, operande: NoeudAST):
        self.op = op
        self.operande = operande

    def evaluer(self, env: dict) -> float:
        val = self.operande.evaluer(env)
        if self.op == "-":
            return -val
        if self.op == "+":
            return val
        raise ValueError(f"Opérateur unaire inconnu : {self.op}")

    def __repr__(self):
        return f"NoeudUnaire('{self.op}', {self.operande})"


class NoeudFonction(NoeudAST):
    """Appel de fonction à un argument : sqrt, abs, log, log2, exp."""

    FONCTIONS = {
        "sqrt": math.sqrt,
        "abs": abs,
        "log": math.log,
        "log2": math.log2,
        "exp": math.exp,
    }

    def __init__(self, nom: str, argument: NoeudAST):
        self.nom = nom.lower()
        self.argument = argument
        if self.nom not in self.FONCTIONS:
            raise NameError(
                f"Fonction inconnue : '{nom}'. "
                f"Fonctions disponibles : {sorted(self.FONCTIONS)}"
            )

    def evaluer(self, env: dict) -> float:
        return self.FONCTIONS[self.nom](self.argument.evaluer(env))

    def __repr__(self):
        return f"NoeudFonction('{self.nom}', {self.argument})"
