"""
phi_complexity/formules/parser.py — Parseur du langage de formules.

Dérivé du parseur d'expressions de la calculatrice GNS-754 (Tomy Verreault, 2026) :
tokenizer à regex + descente récursive. La chaîne de précédences d'origine
(+,- < *,/ < ^ associatif à droite < unaires < primaire) est conservée et
étendue par le bas : or < and < not < comparaisons.

Grammaire :
    ou         := et ("or" et)*
    et         := non ("and" non)*
    non        := "not" non | comparaison
    comparaison:= expr ((">="|"<="|"=="|"!="|">"|"<") expr)?
    expr       := terme (("+"|"-") terme)*
    terme      := facteur (("*"|"/") facteur)*
    facteur    := unaire ("^" facteur)?
    unaire     := ("+"|"-") unaire | primaire
    primaire   := NOMBRE | CONSTANTE | IDENTIFIANT | FONCTION "(" ou ")" | "(" ou ")"
"""
import re
from typing import List, Tuple

from .nodes import (
    NoeudAST, NoeudNombre, NoeudConstante, NoeudIdentifiant,
    NoeudBinaire, NoeudComparaison, NoeudEt, NoeudOu, NoeudNon,
    NoeudUnaire, NoeudFonction,
)


class ParseurFormules:
    """Parseur récursif descendant : chaîne -> arbre de syntaxe abstraite."""

    SPEC_TOKENS = [
        ("NOMBRE", r"\d+(\.\d+)?([eE][+-]?\d+)?"),   # Entiers, flottants, notation scientifique
        ("IDENT",  r"[A-Za-z_][A-Za-z0-9_]*"),        # Identifiants, constantes, mots-clés, fonctions
        ("OP",     r">=|<=|==|!=|>|<|\+|-|\*|/|\^"),  # Opérateurs (multi-caractères d'abord)
        ("LPAREN", r"\("),
        ("RPAREN", r"\)"),
        ("SKIP",   r"[ \t\n]+"),
        ("MISMATCH", r"."),
    ]

    COMPARAISONS = {">", "<", ">=", "<=", "==", "!="}
    MOTS_CLES = {"and", "or", "not"}

    def __init__(self):
        self.regex = "|".join(
            f"(?P<{nom}>{motif})" for nom, motif in self.SPEC_TOKENS
        )

    # ── Lexer ──────────────────────────────────────────────

    def tokenize(self, expression: str) -> List[Tuple[str, str]]:
        """Découpe la chaîne en liste de tokens (type, valeur)."""
        tokens = []
        for m in re.finditer(self.regex, expression):
            kind = m.lastgroup
            value = m.group()
            if kind == "SKIP":
                continue
            if kind == "MISMATCH":
                raise SyntaxError(f"Caractère non reconnu : '{value}' dans la formule.")
            tokens.append((kind, value))
        return tokens

    def parse(self, expression: str) -> NoeudAST:
        """Point d'entrée : chaîne -> racine de l'AST."""
        tokens = self.tokenize(expression)
        if not tokens:
            raise ValueError("Formule vide.")
        self.tokens = tokens
        self.pos = 0
        noeud = self._ou()
        if self.pos < len(self.tokens):
            raise SyntaxError(
                f"Jeton inattendu à la fin : '{self.tokens[self.pos][1]}'"
            )
        return noeud

    # ── Utilitaires ────────────────────────────────────────

    def _peek(self) -> Tuple[str, str]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return ("EOF", "")

    def _consume(self, kind_attendu: str = None) -> Tuple[str, str]:
        kind, val = self._peek()
        if kind_attendu and kind != kind_attendu:
            raise SyntaxError(f"Attendu '{kind_attendu}', trouvé '{val}'")
        self.pos += 1
        return kind, val

    def _est_mot_cle(self, mot: str) -> bool:
        kind, val = self._peek()
        return kind == "IDENT" and val.lower() == mot

    # ── Niveaux de précédence (du plus faible au plus fort) ──

    def _ou(self) -> NoeudAST:
        noeud = self._et()
        while self._est_mot_cle("or"):
            self._consume("IDENT")
            droite = self._et()
            noeud = NoeudOu(noeud, droite)
        return noeud

    def _et(self) -> NoeudAST:
        noeud = self._non()
        while self._est_mot_cle("and"):
            self._consume("IDENT")
            droite = self._non()
            noeud = NoeudEt(noeud, droite)
        return noeud

    def _non(self) -> NoeudAST:
        if self._est_mot_cle("not"):
            self._consume("IDENT")
            return NoeudNon(self._non())
        return self._comparaison()

    def _comparaison(self) -> NoeudAST:
        noeud = self._expr()
        kind, val = self._peek()
        if kind == "OP" and val in self.COMPARAISONS:
            self._consume("OP")
            droite = self._expr()
            noeud = NoeudComparaison(noeud, val, droite)
        return noeud

    def _expr(self) -> NoeudAST:
        """Additions et soustractions."""
        noeud = self._terme()
        while True:
            kind, val = self._peek()
            if kind == "OP" and val in ("+", "-"):
                self._consume("OP")
                droite = self._terme()
                noeud = NoeudBinaire(noeud, val, droite)
            else:
                return noeud

    def _terme(self) -> NoeudAST:
        """Multiplications et divisions."""
        noeud = self._facteur()
        while True:
            kind, val = self._peek()
            if kind == "OP" and val in ("*", "/"):
                self._consume("OP")
                droite = self._facteur()
                noeud = NoeudBinaire(noeud, val, droite)
            else:
                return noeud

    def _facteur(self) -> NoeudAST:
        """Puissance, associativité à droite."""
        noeud = self._unaire()
        kind, val = self._peek()
        if kind == "OP" and val == "^":
            self._consume("OP")
            droite = self._facteur()
            noeud = NoeudBinaire(noeud, "^", droite)
        return noeud

    def _unaire(self) -> NoeudAST:
        """Opérateurs unaires -x, +x."""
        kind, val = self._peek()
        if kind == "OP" and val in ("+", "-"):
            self._consume("OP")
            return NoeudUnaire(val, self._unaire())
        return self._primaire()

    def _primaire(self) -> NoeudAST:
        """Nombres, constantes, identifiants, appels de fonction, parenthèses."""
        kind, val = self._peek()
        if kind == "NOMBRE":
            self._consume("NOMBRE")
            return NoeudNombre(float(val))
        if kind == "IDENT":
            self._consume("IDENT")
            nom = val
            # Appel de fonction : nom suivi de '('
            if self._peek()[0] == "LPAREN":
                self._consume("LPAREN")
                arg = self._ou()
                self._consume("RPAREN")
                return NoeudFonction(nom, arg)
            if nom.lower() in NoeudConstante.CONSTANTES:
                return NoeudConstante(nom)
            if nom.lower() in self.MOTS_CLES:
                raise SyntaxError(
                    f"Mot-clé '{nom}' mal placé dans la formule."
                )
            return NoeudIdentifiant(nom)
        if kind == "LPAREN":
            self._consume("LPAREN")
            noeud = self._ou()
            self._consume("RPAREN")
            return noeud
        raise SyntaxError(f"Syntaxe invalide près de : '{val}'")
