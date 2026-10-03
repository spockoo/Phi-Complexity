"""Parseur Lean 4 autonome — propriété de phi-complexity, stdlib uniquement.

Remplace tree-sitter sur le chemin Lean (décision Tomy 2026-10-03,
principe d'autonomie stricte) : aucun appel externe, aucune grammaire
opaque, `import` limités à la stdlib Python.

Périmètre EXPLICITE : extraction de DÉCLARATIONS de premier niveau
(en-têtes complets + spans de corps + métriques). Le contenu des
termes/tactiques reste opaque, comme avant.

Grammaire supportée = ce que le corpus contient (176 fichiers
lean-navier-stokes, inventaire 2026-10-03) :
- modificateurs : private | protected | noncomputable | unsafe | partial
- mots-clés : def theorem lemma abbrev instance example structure class
  inductive axiom opaque
- attributs @[...] (même ligne ou lignes précédentes), docstrings /-- -/
- noms pointés, paramètres d'univers .{u}, binders (), {}, [], ⦃⦄
- corps après := | where ; axiom/opaque sans corps
- constructeurs d'inductifs après `where` ou `|` (comptés dans le corps)

Principe : tout construit hors grammaire → avertissement explicite avec
ligne, JAMAIS de silence (l'instrument échoue bruyamment).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

@dataclass
class Token:
    """Un token avec sa position (ligne 1-based, col 0-based)."""
    type: str   # 'ident' | 'string' | 'number' | 'op' | 'nl' | 'doc'
    text: str
    line: int
    col: int


@dataclass
class Avertissement:
    """Un construit non reconnu — explicite, jamais silencieux."""
    ligne: int
    message: str


# Mots-clés de déclaration supportés (inventaire corpus 2026-10-03).
DECL_KEYWORDS = frozenset({
    'def', 'theorem', 'lemma', 'abbrev', 'instance', 'example',
    'structure', 'class', 'inductive', 'axiom', 'opaque',
})

# Kinds comptés par l'analyseur (périmètre historique, inchangé) :
# `example`, `axiom`, `opaque` sont EXTRAITS mais non comptés —
# le repli ne répare pas, il n'étend pas (décision 2026-10-02).
KINDS_COMPTES = frozenset({
    'theorem', 'lemma', 'def', 'abbrev', 'instance',
    'structure', 'class', 'inductive',
})

MODIFIERS = frozenset({'private', 'protected', 'noncomputable', 'unsafe', 'partial'})

# Paires de crochets suivies par le lexer / le parseur.
BRACKETS_OPEN = {'(': ')', '{': '}', '[': ']', '⟨': '⟩', '⦃': '⦄', '‹': '›'}
BRACKETS_CLOSE = {v: k for k, v in BRACKETS_OPEN.items()}

_OPS_2 = {':=', '=>', '<;>'}


def _maj_pos(texte: str, line: int, col: int) -> Tuple[int, int]:
    """Avance (line, col) après avoir consommé `texte`."""
    nl = texte.count('\n')
    if nl:
        return line + nl, len(texte) - (texte.rfind('\n') + 1)
    return line, col + len(texte)


def tokenize(code: str) -> Tuple[List[Token], List[Avertissement], int]:
    """Découpe le source en tokens.

    Retourne (tokens, avertissements, nb_commentaires). Les commentaires
    `--` et `/- -/` sont sautés (imbrication gérée) ; les docstrings
    `/-- -/` sont émises comme tokens 'doc' (elles délimitent les spans).
    Ne lève jamais : tout problème de lexage est un avertissement.
    """
    code = code.replace('\r\n', '\n').replace('\r', '\n')
    toks: List[Token] = []
    avs: List[Avertissement] = []
    nb_comments = 0
    n = len(code)
    i = 0
    line, col = 1, 0

    while i < n:
        c = code[i]
        # Nouvelle ligne
        if c == '\n':
            toks.append(Token('nl', '\n', line, col))
            line += 1
            col = 0
            i += 1
            continue
        # Blancs
        if c in ' \t':
            col += 1
            i += 1
            continue
        # Commentaire de ligne --
        if c == '-' and i + 1 < n and code[i + 1] == '-':
            nb_comments += 1
            j = code.find('\n', i)
            if j == -1:
                j = n
            # avance sans émettre (le '\n' sera traité à l'itération suivante)
            col += j - i
            i = j
            continue
        # Commentaire de bloc /- -/ ou docstring /-- -/
        if c == '/' and i + 1 < n and code[i + 1] == '-':
            nb_comments += 1
            if i + 2 < n and code[i + 2] == '-':
                # Docstring : jusqu'au premier '-/' (pas d'imbrication).
                j = code.find('-/', i + 3)
                if j == -1:
                    avs.append(Avertissement(line, 'docstring non terminée'))
                    j = n - 2
                texte = code[i:j + 2]
                toks.append(Token('doc', texte, line, col))
                line, col = _maj_pos(texte, line, col)
                i = j + 2
                continue
            # Bloc imbricable.
            depth = 1
            j = i + 2
            l2, c2 = line, col + 2
            while j < n and depth > 0:
                if code[j] == '/' and j + 1 < n and code[j + 1] == '-':
                    depth += 1
                    j += 2
                    c2 += 2
                elif code[j] == '-' and j + 1 < n and code[j + 1] == '/':
                    depth -= 1
                    j += 2
                    c2 += 2
                else:
                    if code[j] == '\n':
                        l2 += 1
                        c2 = 0
                    else:
                        c2 += 1
                    j += 1
            if depth > 0:
                avs.append(Avertissement(line, 'commentaire de bloc non terminé'))
            line, col = l2, c2
            i = j
            continue
        # Chaîne "..." (échappements gérés)
        if c == '"':
            j = i + 1
            while j < n and code[j] != '"':
                if code[j] == '\\':
                    j += 2
                else:
                    j += 1
            if j >= n:
                avs.append(Avertissement(line, 'chaîne non terminée'))
                j = n - 1
            texte = code[i:j + 1]
            toks.append(Token('string', texte, line, col))
            line, col = _maj_pos(texte, line, col)
            i = j + 1
            continue
        # Littéral caractère 'x' (le ' collé à un identifiant appartient à l'identifiant)
        if c == "'":
            j = i + 1
            if j < n and code[j] == '\\':
                j += 2
            elif j < n:
                j += 1
            if j < n and code[j] == "'":
                texte = code[i:j + 1]
                toks.append(Token('string', texte, line, col))
                line, col = _maj_pos(texte, line, col)
                i = j + 1
                continue
            toks.append(Token('op', "'", line, col))
            col += 1
            i += 1
            continue
        # Nombre
        if c.isdigit():
            j = i
            while j < n and (code[j].isdigit() or code[j] == '_'):
                j += 1
            toks.append(Token('number', code[i:j], line, col))
            col += j - i
            i = j
            continue
        # Identifiant (unicode ok ; ' final inclus : foo')
        if c.isalpha() or c == '_':
            j = i
            while j < n and (code[j].isalnum() or code[j] in "_'"):
                j += 1
            toks.append(Token('ident', code[i:j], line, col))
            col += j - i
            i = j
            continue
        # Opérateurs multi-caractères
        if code[i:i + 3] == '<;>':
            toks.append(Token('op', '<;>', line, col))
            col += 3
            i += 3
            continue
        if code[i:i + 2] in _OPS_2:
            toks.append(Token('op', code[i:i + 2], line, col))
            col += 2
            i += 2
            continue
        # Tout le reste : opérateur / ponctuation à 1 caractère
        toks.append(Token('op', c, line, col))
        col += 1
        i += 1

    return toks, avs, nb_comments


# ---------------------------------------------------------------------------
# Parseur de déclarations
# ---------------------------------------------------------------------------

@dataclass
class DeclAutonome:
    """Une déclaration extraite par le parseur autonome."""
    kind: str            # mot-clé source : def, theorem, lemma, ...
    nom: str             # nom pointé (sans .{u})
    ligne: int           # ligne du mot-clé (1-based)
    ligne_fin: int       # dernière ligne du span (corps inclus)
    ligne_debut: int     # première ligne du span (docstring/attributs inclus)
    texte: str           # span source complet
    corps: str           # texte après ':=' / 'where' ('' si axiom/opaque)
    type_retour: str     # texte du type de retour ('' si absent)
    nb_args: int         # identifiants liés dans les binders
    binders: list = field(default_factory=list)  # [(nom, implicite)]
    nb_tactiques: int = 0
    profondeur_by: int = 0
    a_preuve: bool = False


class _Parser:
    def __init__(self, toks: List[Token], code: str):
        self.toks = toks
        self.n = len(toks)
        self.code = code.replace('\r\n', '\n').replace('\r', '\n')
        self.lignes = self.code.split('\n')
        # Offsets de début de ligne pour les spans exacts.
        self._offsets = [0]
        for ln in self.lignes:
            self._offsets.append(self._offsets[-1] + len(ln) + 1)
        self.avs: List[Avertissement] = []
        self.decls: List[DeclAutonome] = []

    # -- utilitaires ------------------------------------------------------
    def avertir(self, ligne: int, message: str) -> None:
        self.avs.append(Avertissement(ligne, message))

    def _sig(self, i: int, sauter_doc: bool = True) -> int:
        """Prochain index significatif (saute 'nl', et 'doc' si demandé)."""
        while i < self.n and (self.toks[i].type == 'nl'
                              or (sauter_doc and self.toks[i].type == 'doc')):
            i += 1
        return i


    def _ouvrant_apparie(self, i_fermant: int) -> Optional[int]:
        """Retrouve l'ouvrant apparié à un fermant (recherche arrière)."""
        if self.toks[i_fermant].text not in BRACKETS_CLOSE:
            return None
        depth = 0
        j = i_fermant
        while j >= 0:
            t = self.toks[j].text
            if t in BRACKETS_CLOSE:
                depth += 1
            elif t in BRACKETS_OPEN:
                depth -= 1
                if depth == 0:
                    return j
            j -= 1
        return None

    def _skip_balanced(self, i: int) -> int:
        """Saute un groupe équilibré ; i pointe sur l'ouvrant. Retourne
        l'index APRÈS le fermant. Avertit (sans lever) si non fermé."""
        if i >= self.n or self.toks[i].text not in BRACKETS_OPEN:
            return i
        depth = 0
        j = i
        while j < self.n:
            t = self.toks[j].text
            if t in BRACKETS_OPEN:
                depth += 1
            elif t in BRACKETS_CLOSE:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        self.avertir(self.toks[i].line,
                     f"groupe '{self.toks[i].text}' non fermé")
        return self.n

    def _skip_line(self, i: int) -> int:
        while i < self.n and self.toks[i].type != 'nl':
            i += 1
        return i

    # -- boucle principale -------------------------------------------------
    def run(self) -> Tuple[List[DeclAutonome], List[Avertissement]]:
        toks, n = self.toks, self.n
        i = 0
        depth = 0
        while i < n:
            t = toks[i]
            if t.type == 'nl':
                i += 1
                continue
            # Docstring : peut ouvrir un préfixe de déclaration — tester
            # avant de sauter (sinon la docstring est exclue du span).
            if t.type == 'doc':
                if t.col == 0 and depth == 0:
                    kw_idx = self._debut_decl(i)
                    if kw_idx is not None:
                        _, i_start = self._scan_prefix(i)
                        i = self._parse_declaration(kw_idx, i_start)
                        continue
                i += 1
                continue
            # Commandes #... : sauter la ligne
            if t.text == '#' and t.type == 'op':
                i = self._skip_line(i)
                continue
            # Début de déclaration : préfixe en colonne 0 (docstring,
            # attributs @[...], modificateurs) puis mot-clé. Le mot-clé
            # seul n'est pas forcément en colonne 0 (ex. `noncomputable
            # def`, `@[simp] theorem`) — c'est le DÉBUT du préfixe qui
            # compte.
            if depth == 0:
                kw_idx = self._debut_decl(i)
                if kw_idx is not None:
                    _, i_start = self._scan_prefix(i)
                    i = self._parse_declaration(kw_idx, i_start)
                    continue
            # Mot-clé de déclaration hors préfixe colonne 0 : ignoré,
            # bruyamment (jamais de silence).
            if depth == 0 and t.type == 'ident' and t.text in DECL_KEYWORDS:
                self.avertir(t.line,
                             f"mot-clé '{t.text}' hors préfixe colonne 0 "
                             f"(col {t.col}) — ignoré, jamais silencieux")
                i += 1
                continue
            # 'mutual' : hors grammaire supportée → bruyant, pas silencieux
            if depth == 0 and t.type == 'ident' and t.text == 'mutual':
                self.avertir(t.line,
                             "'mutual' non supporté par le parseur autonome — "
                             "bloc ignoré jusqu'au prochain 'end' en colonne 0")
                i = self._skip_to_end_col0(i)
                continue
            if t.text in BRACKETS_OPEN:
                depth += 1
            elif t.text in BRACKETS_CLOSE:
                depth = max(0, depth - 1)
            i += 1
        return self.decls, self.avs

    def _scan_prefix(self, i: int) -> Optional[Tuple[int, int]]:
        """Depuis un début de préfixe (colonne 0), consomme
        [docstring | @[...] | modificateur | nl]* puis un mot-clé de
        déclaration. Retourne (index_mot_clé, index_début) ou None."""
        toks, n = self.toks, self.n
        k = i
        while k < n:
            t = toks[k]
            if t.type in ('nl', 'doc'):
                k += 1
                continue
            if t.text == '@':
                m = self._sig(k + 1, sauter_doc=False)
                if m < n and toks[m].text == '[':
                    k = self._skip_balanced(m)
                    continue
                return None
            if t.type == 'ident' and t.text in MODIFIERS:
                k += 1
                continue
            if t.type == 'ident' and t.text in DECL_KEYWORDS:
                return (k, i)
            return None
        return None

    def _debut_decl(self, i: int) -> Optional[int]:
        """Si un préfixe de déclaration commence à toks[i] (colonne 0 :
        docstring, attributs @[...], modificateurs, puis mot-clé),
        retourne l'index du mot-clé, sinon None."""
        t = self.toks[i]
        if not (t.col == 0 and (t.type == 'doc' or t.text == '@'
                or (t.type == 'ident' and (t.text in MODIFIERS
                                           or t.text in DECL_KEYWORDS)))):
            return None
        res = self._scan_prefix(i)
        return res[0] if res is not None else None

    def _skip_to_end_col0(self, i: int) -> int:
        toks, n = self.toks, self.n
        while i < n:
            t = toks[i]
            if (t.type == 'ident' and t.text == 'end' and t.col == 0):
                return self._skip_line(i)
            i += 1
        return n


    # -- parsing d'une déclaration -----------------------------------------
    def _parse_declaration(self, i_kw: int, i_start: int) -> int:
        """Parse une déclaration : i_kw = index du mot-clé, i_start = index
        du premier token du préfixe (docstring / attributs / modificateurs).
        Ajoute à self.decls. Retourne l'index de reprise (début de la
        déclaration suivante ou fin du fichier)."""
        toks, n = self.toks, self.n
        k = i_kw
        kw = toks[k].text
        ligne_kw = toks[k].line
        ligne_debut = toks[i_start].line

        j = k + 1
        nom: Optional[str] = None
        binders: List[Tuple[str, bool]] = []  # (nom, implicite)
        type_retour = ''
        # `instance` : groupes initiaux (priorité...) puis nom ou anonyme.
        if kw == 'instance':
            while j < n and toks[j].text in BRACKETS_OPEN:
                j = self._skip_balanced(j)
                j = self._sig(j, sauter_doc=False)
        # Nom pointé (avec paramètres d'univers .{u} éventuels).
        if j < n and toks[j].type == 'ident':
            parts = [toks[j].text]
            j += 1
            while (j + 1 < n and toks[j].text == '.'
                   and toks[j + 1].type == 'ident'):
                parts.append(toks[j + 1].text)
                j += 2
            # Paramètres d'univers .{u, v} — sautés (le nom reste sans .{u}).
            if (j + 1 < n and toks[j].text == '.'
                    and toks[j + 1].text == '{'):
                j = self._skip_balanced(j + 1)
                while (j + 1 < n and toks[j].text == '.'
                       and toks[j + 1].type == 'ident'):
                    parts.append(toks[j + 1].text)
                    j += 2
            nom = '.'.join(parts)
        if nom is None:
            # Instance anonyme (`instance : T := ...`) — ignorée comme les
            # extracteurs actuels (documenté, pas de silence : comptée).
            self.avertir(ligne_kw,
                         f"instance anonyme ignorée (ligne {ligne_kw})")
            return self._fin_entete_sans_nom(j)
        # Binders : (x : T) {x : T} [inst] ⦃x⦄ — groupes équilibrés.
        # Ils peuvent commencer sur la ligne suivante (style corpus :
        # `theorem foo\n    (a : T) : ...`) — on saute les 'nl'.
        nb_args = 0
        j = self._sig(j, sauter_doc=False)
        while j < n and toks[j].text in BRACKETS_OPEN:
            ouv = toks[j].text
            implicite = ouv in ('{', '[', '⦃')
            # Noms = identifiants avant ':' au premier niveau du groupe.
            noms_groupe = self._noms_binder(j)
            for nm in noms_groupe:
                binders.append((nm, implicite))
            nb_args += len(noms_groupe)
            j = self._skip_balanced(j)
            j = self._sig(j, sauter_doc=False)
        # Type de retour optionnel.
        # `extends` (structure/class) : sauter les parents avant
        # le 'where' / ':=' (ex. `structure S ... extends P Q where`).
        if (j < n and toks[j].type == 'ident' and toks[j].text == 'extends'):
            j += 1
            depth_e = 0
            while j < n:
                te = toks[j]
                if te.text in BRACKETS_OPEN:
                    depth_e += 1
                elif te.text in BRACKETS_CLOSE:
                    depth_e = max(0, depth_e - 1)
                elif depth_e == 0 and (
                        te.text == ':=' or (te.type == 'ident'
                                            and te.text == 'where')):
                    break
                j += 1
        if j < n and toks[j].text == ':':
            j += 1
            t0 = j
            depth = 0
            while j < n:
                t = toks[j]
                if t.text in BRACKETS_OPEN:
                    depth += 1
                elif t.text in BRACKETS_CLOSE:
                    depth -= 1
                elif depth == 0 and t.text == ':=':
                    break
                elif depth == 0 and t.type == 'ident' and t.text == 'where':
                    break
                elif depth == 0 and t.text == '|' and self._debut_ligne(j):
                    # Définition par filtrage sans ':=' ni 'where' :
                    #   def f : T
                    #   | pat => ...
                    # (forme présente dans le corpus : bonySym, etc.)
                    break
                elif depth == 0 and t.type == 'nl':
                    m = self._sig(j)
                    if m >= n:
                        break
                    tm = toks[m]
                    if (tm.type == 'ident' and tm.text in DECL_KEYWORDS
                            and self._debut_ligne(m)):
                        break  # axiom/opaque sans corps : fin d'en-tête
                        # (colonne 0 ou indenté : dans les deux cas on ne
                        # doit pas avaler le mot-clé dans le type)
                j += 1
            type_retour = self._texte_tokens(t0, j).strip()
        # Terminateur d'en-tête.
        has_body = True
        if j < n and toks[j].text == ':=':
            j += 1
        elif j < n and toks[j].type == 'ident' and toks[j].text == 'where':
            j += 1
        elif j < n and toks[j].text == '|':
            pass  # filtrage : le corps (branches) commence ici
        elif kw in ('axiom', 'opaque'):
            has_body = False
        else:
            self.avertir(ligne_kw,
                         f"en-tête sans ':=' ni 'where' pour '{kw} {nom}' — "
                         f"corps vide supposé")
            has_body = False
        # Corps : jusqu'au début de la déclaration suivante ou EOF.
        fin = self._fin_corps(j)
        # Rogner les lignes vides finales du span.
        while fin > j and toks[fin - 1].type == 'nl':
            fin -= 1
        ligne_fin = toks[fin - 1].line if fin > j else ligne_kw
        # Span exact : du premier token du préfixe à la fin de ligne_fin.
        o_fin = self._offsets[ligne_fin]  # début de la ligne suivante
        t_start = toks[i_start]
        texte = self.code[self._offsets[t_start.line - 1] + t_start.col
                          :o_fin].rstrip('\n')
        if has_body and fin > j:
            corps = self.code[self._offsets[toks[j].line - 1] + toks[j].col
                              :o_fin].strip()
        else:
            corps = ''
        # Métriques tactiques sur le corps.
        toks_corps = toks[j:fin]
        nb_tac, prof_by, tac_avs = _compter_tactiques(toks_corps)
        for a in tac_avs:
            self.avs.append(a)
        self.decls.append(DeclAutonome(
            kind=kw, nom=nom, ligne=ligne_kw, ligne_fin=ligne_fin,
            ligne_debut=ligne_debut, texte=texte, corps=corps,
            type_retour=type_retour, nb_args=nb_args, binders=binders,
            nb_tactiques=nb_tac, profondeur_by=prof_by,
            a_preuve=has_body and nb_tac > 0,
        ))
        return fin

    def _debut_ligne(self, i: int) -> bool:
        """Vrai si toks[i] est le premier token significatif de sa ligne."""
        j = i - 1
        while j >= 0 and self.toks[j].type in ('nl', 'doc'):
            if self.toks[j].type == 'nl':
                return True
            j -= 1
        return j < 0

    def _noms_binder(self, i_ouvrant: int) -> List[str]:
        """Noms liés dans un groupe binder : identifiants avant ':' au
        premier niveau. Pour un lieur d'instance `[T]` sans ':' (ex.
        `[Inhabited Nat]`), aucun nom n'est lié → []. Pour `(x)`, `{x}`,
        `⦃x⦄` sans ':', l'identifiant est lié → [x]."""
        toks = self.toks
        est_instance = toks[i_ouvrant].text == '['
        noms: List[str] = []
        vu_deux_points = False
        depth = 0
        j = i_ouvrant + 1
        while j < self.n:
            t = toks[j]
            if t.text in BRACKETS_OPEN:
                depth += 1
            elif t.text in BRACKETS_CLOSE:
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and t.text == ':':
                vu_deux_points = True
                break
            elif depth == 0 and t.type == 'ident':
                noms.append(t.text)
            j += 1
        if vu_deux_points:
            return noms
        # Pas de ':' : (x) lie x ; [T] ne lie rien.
        return [] if est_instance else noms

    def _texte_tokens(self, a: int, b: int) -> str:
        """Texte source exact des tokens [a, b) via offsets (pas de
        troncature par lignes)."""
        if a >= b or a >= self.n:
            return ''
        b = min(b, self.n)
        t0, t1 = self.toks[a], self.toks[b - 1]
        o0 = self._offsets[t0.line - 1] + t0.col
        o1 = self._offsets[t1.line - 1] + t1.col + len(t1.text)
        return self.code[o0:o1]

    def _fin_entete_sans_nom(self, j: int) -> int:
        """Reprise après une instance anonyme : on saute l'en-tête jusqu'à
        ':=' / 'where' / fin de ligne-déclaration, puis le corps."""
        toks, n = self.toks, self.n
        depth = 0
        while j < n:
            t = toks[j]
            if t.text in BRACKETS_OPEN:
                depth += 1
            elif t.text in BRACKETS_CLOSE:
                depth -= 1
            elif depth == 0 and t.text in (':=',):
                j += 1
                break
            elif depth == 0 and t.type == 'ident' and t.text == 'where':
                j += 1
                break
            elif depth == 0 and t.type == 'nl':
                m = self._sig(j)
                if m >= n:
                    break
                tm = toks[m]
                if tm.type == 'ident' and tm.text in DECL_KEYWORDS \
                        and tm.col == 0:
                    break
            j += 1
        return self._fin_corps(j)

    def _fin_corps(self, j: int) -> int:
        """Index de fin du corps : début de la déclaration suivante ou EOF.

        Le corps se termine devant : un mot-clé de déclaration en position
        de commande, ou un attribut @[...] suivi (lookahead) d'un mot-clé
        de déclaration. Tout le reste (y compris 'deriving', 'end',
        'section', 'namespace') est absorbé dans le corps.
        """
        toks, n = self.toks, self.n
        depth = 0
        i = j
        while i < n:
            t = toks[i]
            if t.type == 'nl':
                i += 1
                continue
            if t.text in BRACKETS_OPEN:
                depth += 1
                i += 1
                continue
            if t.text in BRACKETS_CLOSE:
                depth = max(0, depth - 1)
                i += 1
                continue
            # Début possible de la déclaration suivante (même logique
            # de préfixe que la boucle principale).
            if depth == 0 and self._debut_decl(i) is not None:
                return i
            # Mot-clé de déclaration en début de ligne mais indenté
            # (hors colonne 0) : on ARRÊTE le corps ici au lieu de
            # l'avaler en silence — la boucle principale émettra
            # l'avertissement « hors préfixe colonne 0 » (bruyant,
            # jamais silencieux). Sûr : aucun de ces mots-clés n'est
            # une tactique ni un terme Lean valide en cette position.
            if (depth == 0 and t.type == 'ident' and t.text in DECL_KEYWORDS
                    and t.col > 0 and self._debut_ligne(i)):
                return i
            i += 1
        return n

# ---------------------------------------------------------------------------
# Comptage des tactiques (sans tree-sitter)
# ---------------------------------------------------------------------------
#
# Règle documentée (parseur autonome, 2026-10-03) :
# - un bloc tactique commence après le mot-clé `by` ;
# - C0 = colonne du premier token du premier pas ;
# - nouveau pas : après `;` / `<;>` à profondeur relative 0, ou en début
#   de ligne à profondeur 0 et colonne == C0 (les lignes `|` sont des
#   branches de `match`/`with`, jamais un nouveau pas) ;
# - fin du bloc : profondeur relative < 0 (crochet fermant d'avant le `by`),
#   ou début de ligne à profondeur 0 et colonne < C0 (dédent), ou EOF ;
# - un `by` imbriqué est scanné récursivement (pas comptés, profondeur +1).
#
# Divergence assumée vs tree-sitter : une invocation de tactique sur
# plusieurs lignes DONT les lignes de suite sont à colonne == C0 compte
# un pas par ligne ici (tree-sitter, qui parse la tactique, n'en compte
# qu'un). Mesuré sur le corpus : voir le harnais de comparaison.

def _compter_tactiques(toks: List[Token]) -> Tuple[int, int, List[Avertissement]]:
    """(nb_pas, profondeur_by_max, alertes) sur les tokens d'un corps."""
    total = 0
    prof_max = 0
    alertes: List[Avertissement] = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t.type == 'ident' and t.text == 'by':
            nb, pr, i2, al = _scan_by(toks, i)
            total += nb
            prof_max = max(prof_max, pr)
            alertes.extend(al)
            i = i2
        else:
            i += 1
    return total, prof_max, alertes


def _scan_by(toks: List[Token], i: int) -> Tuple[int, int, int, List[Avertissement]]:
    """Scanne un bloc `by` à partir de toks[i] == 'by'.

    Retourne (nb_pas, profondeur, index_reprise, alertes)."""
    alertes: List[Avertissement] = []
    n = len(toks)
    j = i + 1
    while j < n and toks[j].type == 'nl':
        j += 1
    if j >= n:
        alertes.append(Avertissement(
            toks[i].line, "'by' en fin de corps — aucun pas compté"))
        return 0, 1, n, alertes
    C0 = toks[j].col
    steps = 1
    maxd = 1
    depth = 0
    k = j
    while k < n:
        t = toks[k]
        if t.type == 'nl':
            m = k + 1
            while m < n and toks[m].type == 'nl':
                m += 1
            if m >= n:
                k = m
                break
            nt = toks[m]
            if depth == 0 and nt.col < C0:
                break  # dédent : fin du bloc tactique
            if depth == 0 and nt.col == C0 and nt.text != '|':
                steps += 1
            k = m
            continue
        if t.text in BRACKETS_OPEN:
            depth += 1
        elif t.text in BRACKETS_CLOSE:
            depth -= 1
            if depth < 0:
                break  # crochet d'avant le `by` : fin du bloc
        elif depth == 0 and t.type == 'ident' and t.text == 'by':
            nb2, pr2, k2, al2 = _scan_by(toks, k)
            total2 = nb2
            steps += total2
            maxd = max(maxd, 1 + pr2)
            alertes.extend(al2)
            k = k2
            continue
        elif depth == 0 and t.text in (';', '<;>'):
            m = k + 1
            while m < n and toks[m].type == 'nl':
                m += 1
            if m < n:
                steps += 1
                k = m
                continue
        k += 1
    return steps, maxd, k, alertes


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

@dataclass
class ResultatParse:
    declarations: List[DeclAutonome]
    avertissements: List[Avertissement]
    nb_commentaires: int


def parse(code: str) -> ResultatParse:
    """Parse autonome d'un fichier Lean : déclarations + avertissements.

    Ne lève jamais ; tout construit non reconnu est un avertissement
    explicite avec numéro de ligne.
    """
    toks, avs_lex, nb_comments = tokenize(code)
    parser = _Parser(toks, code)
    decls, avs_parse = parser.run()
    # Métriques tactiques déjà calculées dans _parse_declaration ; on
    # propage ici les alertes éventuelles du scan (aucune pour l'instant :
    # _compter_tactiques est appelé dans _parse_declaration).
    return ResultatParse(declarations=decls,
                         avertissements=avs_lex + avs_parse,
                         nb_commentaires=nb_comments)


def parse_declarations(code: str) -> List[DeclAutonome]:
    """Raccourci : uniquement les déclarations."""
    return parse(code).declarations


def identifiants(toks: List[Token]) -> Iterator[Tuple[str, int]]:
    """Itère (nom, ligne) sur les tokens identifiants — pour le couplage,
    sans tree-sitter."""
    for t in toks:
        if t.type == 'ident':
            yield t.text, t.line

# ---------------------------------------------------------------------------
# Convertisseur vers le modèle partagé + API de remplacement
# ---------------------------------------------------------------------------

def vers_declaration(d: DeclAutonome):
    """Convertit une DeclAutonome vers le dataclass partagé Declaration
    (parseur_lean), avec extraction="autonome"."""
    from .parseur_lean import Binder, Declaration
    binders = [
        Binder(nom=nom, type="", implicite=impl)
        for nom, impl in d.binders
    ]
    return Declaration(
        kind=d.kind,
        nom=d.nom,
        binders=binders,
        type_retour=d.type_retour,
        corps=d.corps,
        a_preuve=d.a_preuve,
        texte=d.texte,
        ligne=d.ligne,
        extraction="autonome",
        ligne_fin=d.ligne_fin,
        nb_tactiques=d.nb_tactiques,
        profondeur_by=d.profondeur_by,
    )


def rechercher_declaration_autonome(code: str, nom: str):
    """Recherche une déclaration par nom via le parseur autonome
    (source unique, sans tree-sitter). Retourne None si absente."""
    for d in parse_declarations(code):
        if d.nom == nom:
            return vers_declaration(d)
    return None
