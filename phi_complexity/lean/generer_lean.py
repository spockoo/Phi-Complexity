#!/usr/bin/env python3
"""
generer_lean.py — Génère un module Lean 4.34.0 depuis l'analyse d'un .phiast v2.

Entrée : un .phiast v2.
Analyse : noms + kinds + graphe de dépendances (consts des types/valeurs).
Sortie : module .lean avec les déclarations en ORDRE TOPOLOGIQUE du graphe,
         en-tête de provenance (source, sha256, n_decls).

Couverture (chantier A3, H-ECR-2) :
  kinds : def, theorem, axiom, opaque, inductive (simple + mutuels).
  tags Expr émissibles : bvar, sort, const, app, lam, forallE, letE, lit,
                         proj, ref (substitué au parse).
  tags NON émissibles en source close -> GenerationError BRUYANTE nommant
  la déclaration (jamais d'axiom/sorry silencieux) :
    fvar/mvar libres (le noyau les refuserait — prouvé A2/C8-C9),
    mdata (la spec §2.3 l'exige).

Portable Windows : open(..., "rb"/"w"), struct <LE, pas de os.pread.

Usage :
    python3 generer_lean.py <fichier.phiast> -o <module.lean>
"""

import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lecteur_phiast import MAGIC, TRAILER_MAGIC, VERSION2


class GenerationError(Exception):
    """Le générateur refuse : construction non couverte, nommée."""


class _Curseur(object):
    __slots__ = ("buf", "pos")

    def __init__(self, buf):
        self.buf = buf
        self.pos = 0

    def u8(self):
        if self.pos >= len(self.buf):
            raise GenerationError("binaire tronqué (u8 à %d)" % self.pos)
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def u16(self):
        return struct.unpack("<H", self._raw(2))[0]

    def u32(self):
        return struct.unpack("<I", self._raw(4))[0]

    def u64(self):
        return struct.unpack("<Q", self._raw(8))[0]

    def _raw(self, n):
        if self.pos + n > len(self.buf):
            raise GenerationError("binaire tronqué (%d o à %d)"
                                  % (n, self.pos))
        r = self.buf[self.pos:self.pos + n]
        self.pos += n
        return r


def _parse_fichier(chemin):
    with open(chemin, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    c = _Curseur(data)
    if c._raw(8) != MAGIC:
        raise GenerationError("magic invalide")
    if c.u32() != VERSION2:
        raise GenerationError("version != 2")
    tc_len = c.u16()
    toolchain = c._raw(tc_len).decode("utf-8")
    n_decls = c.u32()
    c.u32()  # flags
    body_start = c.pos
    strtab_off = struct.unpack("<Q", data[-16:-8])[0]
    if data[-8:] != TRAILER_MAGIC:
        raise GenerationError("trailer invalide")
    # strtab
    c.pos = strtab_off
    n_str = c.u32()
    strtab = []
    for _ in range(n_str):
        ln = c.u16()
        strtab.append(c._raw(ln).decode("utf-8"))
    if c.pos != len(data) - 16:
        raise GenerationError("strtab mal aboutée")

    refs = {}  # offset absolu -> expr (tag 12)

    def get_str(sid, où):
        if sid >= len(strtab):
            raise GenerationError("sid %d invalide (%s)" % (sid, où))
        return strtab[sid]

    def parse_nom():
        pile = []
        while True:
            tag = c.u8()
            if tag == 0:
                break
            if tag > 2:
                raise GenerationError("tag nom %d" % tag)
            pile.append(tag)
        parts = []
        for tag in reversed(pile):
            v = c.u32()
            parts.append(get_str(v, "nom") if tag == 1 else str(v))
        return ".".join(parts)

    def parse_niveau():
        tag = c.u8()
        if tag == 0:
            return ("zero",)
        if tag == 1:
            return ("succ", parse_niveau())
        if tag == 2:
            return ("max", parse_niveau(), parse_niveau())
        if tag == 3:
            return ("imax", parse_niveau(), parse_niveau())
        if tag == 4:
            return ("param", parse_nom())
        if tag == 5:
            return ("mvar", parse_nom())
        raise GenerationError("tag niveau %d non couvert" % tag)

    def parse_expr():
        off = c.pos  # offset absolu : le curseur parcourt tout le fichier
        tag = c.u8()
        if tag == 0:
            e = ("bvar", c.u32())
        elif tag == 1:
            e = ("fvar", parse_nom())
        elif tag == 2:
            e = ("mvar", parse_nom())
        elif tag == 3:
            e = ("sort", parse_niveau())
        elif tag == 4:
            nom = parse_nom()
            k = c.u32()
            e = ("const", nom, [parse_niveau() for _ in range(k)])
        elif tag == 5:
            f = parse_expr()
            a = parse_expr()
            e = ("app", f, a)
        elif tag in (6, 7):
            bn = parse_nom()
            t = parse_expr()
            b = parse_expr()
            bi = c.u8()
            if bi > 3:
                raise GenerationError("BinderInfo %d" % bi)
            e = ("lam" if tag == 6 else ("forall"), bn, t, b, bi)
        elif tag == 8:
            bn = parse_nom()
            t = parse_expr()
            v = parse_expr()
            b = parse_expr()
            nd = c.u8()
            if nd > 1:
                raise GenerationError("nonDep %d" % nd)
            e = ("let", bn, t, v, b, nd)
        elif tag == 9:
            lt = c.u8()
            s = get_str(c.u32(), "lit")
            if lt == 0:
                e = ("nat", int(s))
            elif lt == 1:
                e = ("str", s)
            else:
                raise GenerationError("lit_tag %d" % lt)
        elif tag == 10:
            sn = parse_nom()
            i = c.u32()
            tgt = parse_expr()
            e = ("proj", sn, i, tgt)
        elif tag == 11:
            raise GenerationError("tag mdata : refusé (spec §2.3)")
        elif tag == 12:
            roff = c.u64()
            if roff not in refs:
                raise GenerationError("ref vers offset %d non émis" % roff)
            e = refs[roff]
        else:
            raise GenerationError("tag expr %d non couvert" % tag)
        refs[off] = e
        return e

    c.pos = body_start
    decls = []
    kinds = {0: "def", 1: "theorem", 2: "axiom", 3: "opaque"}
    for i in range(n_decls):
        kind = c.u8()
        if kind == 4:
            n = c.u32()
            if n == 0:
                raise GenerationError("bloc inductif vide (décl #%d)" % i)
            inds = []
            for _ in range(n):
                inm = parse_nom()
                ity = parse_expr()
                nc = c.u32()
                ctors = []
                for _ in range(nc):
                    cnm = parse_nom()
                    cty = parse_expr()
                    ctors.append((cnm, cty))
                inds.append((inm, ity, ctors))
            fournit = [inm for inm, _, _ in inds]
            for _, _, ctors in inds:
                fournit += [cnm for cnm, _ in ctors]
            decls.append({"nom": inds[0][0], "kind": "inductive",
                          "type": None, "value": None,
                          "fournit": fournit, "inductifs": inds})
            continue
        if kind not in kinds:
            raise GenerationError("kind %d (décl #%d)" % (kind, i))
        nom = parse_nom()
        typ = parse_expr()
        hv = c.u8()
        if hv > 1:
            raise GenerationError("has_value %d (%s)" % (hv, nom))
        val = parse_expr() if hv else None
        decls.append({"nom": nom, "kind": kinds[kind],
                      "type": typ, "value": val, "fournit": [nom],
                      "inductifs": None})
    return {"decls": decls, "sha256": sha, "n_decls": n_decls,
            "toolchain": toolchain, "source": chemin}


# ------------------------------------------------------------- dépendances
def _consts(e):
    t = e[0]
    if t == "const":
        return {e[1]}
    if t == "app":
        return _consts(e[1]) | _consts(e[2])
    if t in ("lam", "forall"):
        return _consts(e[2]) | _consts(e[3])
    if t == "let":
        return _consts(e[2]) | _consts(e[3]) | _consts(e[4])
    if t == "proj":
        return _consts(e[3])
    return set()


def _deps_decl(d):
    ds = set()
    if d["kind"] == "inductive":
        for _, ty, ctors in d["inductifs"]:
            ds |= _consts(ty)
            for _, cty in ctors:
                ds |= _consts(cty)
    else:
        for e in (d["type"], d["value"]):
            if e is not None:
                ds |= _consts(e)
    return ds - set(d["fournit"])


def _tri_topologique(decls):
    fournit = {}
    for d in decls:
        for n in d["fournit"]:
            if n in fournit:
                raise GenerationError("nom fourni deux fois : %s" % n)
            fournit[n] = d["nom"]
    noms = [d["nom"] for d in decls]
    par_nom = {d["nom"]: d for d in decls}
    deps = {}
    for d in decls:
        ds = set()
        for c in _deps_decl(d):
            if c in fournit:
                ds.add(fournit[c])
        deps[d["nom"]] = ds - {d["nom"]}
    ordre, sans_dep = [], sorted(n for n in noms if not deps[n])
    deps = {n: set(v) for n, v in deps.items()}
    while sans_dep:
        n = sans_dep.pop(0)
        ordre.append(n)
        for m in sorted(deps):
            if n in deps[m]:
                deps[m].remove(n)
                if not deps[m]:
                    sans_dep.append(m)
        sans_dep.sort()
    if len(ordre) != len(noms):
        restants = sorted(set(noms) - set(ordre))
        raise GenerationError("cycle de dépendances : %s" % restants)
    return [par_nom[n] for n in ordre]


# ---------------------------------------------------------------- émission
def _emettre_niveau(l):
    t = l[0]
    if t == "zero":
        return "0"
    if t == "succ":
        n = 0
        x = l
        while x[0] == "succ":
            n += 1
            x = x[1]
        base = _emettre_niveau(x)
        if base == "0":
            return str(n)
        return "(%s+%d)" % (base, n)
    if t == "max":
        return "(max %s %s)" % (_emettre_niveau(l[1]),
                               _emettre_niveau(l[2]))
    if t == "imax":
        return "(imax %s %s)" % (_emettre_niveau(l[1]),
                                _emettre_niveau(l[2]))
    if t == "param":
        return l[1].split(".")[-1]
    raise GenerationError("niveau non émissible : %r" % (l,))


def _emettre_sort(l):
    n = 0
    x = l
    while x[0] == "succ":
        n += 1
        x = x[1]
    if x[0] == "zero":
        if n == 0:
            return "Prop"
        if n == 1:
            return "Type"
        return "Type %d" % (n - 1)
    base = _emettre_niveau(x)
    if n == 0:
        return "Sort %s" % base
    return "Sort (%s+%d)" % (base, n)


def _params_niveau(l, acc):
    t = l[0]
    if t == "param":
        if l[1] not in acc:
            acc.append(l[1])
    elif t == "mvar":
        raise GenerationError("niveau mvar non émissible : %s" % l[1])
    elif t == "succ":
        _params_niveau(l[1], acc)
    elif t in ("max", "imax"):
        _params_niveau(l[1], acc)
        _params_niveau(l[2], acc)


def _params_expr(e, acc):
    t = e[0]
    if t == "sort":
        _params_niveau(e[1], acc)
    elif t == "const":
        for l in e[2]:
            _params_niveau(l, acc)
    elif t == "app":
        _params_expr(e[1], acc)
        _params_expr(e[2], acc)
    elif t in ("lam", "forall"):
        _params_expr(e[2], acc)
        _params_expr(e[3], acc)
    elif t == "let":
        _params_expr(e[2], acc)
        _params_expr(e[3], acc)
        _params_expr(e[4], acc)
    elif t == "proj":
        _params_expr(e[3], acc)


def _level_params(d):
    acc = []
    if d["kind"] == "inductive":
        for _, ty, ctors in d["inductifs"]:
            _params_expr(ty, acc)
            for _, cty in ctors:
                _params_expr(cty, acc)
    else:
        for e in (d["type"], d["value"]):
            if e is not None:
                _params_expr(e, acc)
    return [n.split(".")[-1] for n in acc]


_ATOMIQUES = ("bvar", "const", "nat", "str")


def _emettre_expr(e, pile):
    """pile : noms des lieurs (le plus interne en dernier) pour les bvar."""
    t = e[0]
    if t == "bvar":
        i = e[1]
        if i >= len(pile):
            raise GenerationError("bvar %d hors portée (%d lieurs)"
                                  % (i, len(pile)))
        return pile[-1 - i]
    if t == "sort":
        return _emettre_sort(e[1])
    if t == "const":
        nom, lvs = e[1], e[2]
        if lvs:
            return "%s.{%s}" % (nom, ", ".join(_emettre_niveau(l)
                                              for l in lvs))
        return nom
    if t == "nat":
        return str(e[1])
    if t == "str":
        return '"%s"' % e[1].replace("\\", "\\\\").replace('"', '\\"')
    if t == "fvar":
        raise GenerationError("fvar libre non émissible en source : %s"
                              % e[1])
    if t == "mvar":
        raise GenerationError("mvar libre non émissible en source : %s"
                              % e[1])
    if t == "app":
        args = []
        f = e
        while f[0] == "app":
            args.append(f[2])
            f = f[1]
        args.reverse()
        sf = _emettre_expr(f, pile)
        # `@` : dans le terme noyau, TOUS les arguments (y compris les
        # implicites) sont explicites ; `@f` est la traduction fidèle en
        # source (ex. `@Eq.refl.{1} Nat 2` — sans `@`, Lean met `Nat`
        # sur le premier paramètre *explicite* et l'élaboration échoue).
        if f[0] == "const":
            sf = "@" + sf
        elif f[0] in ("lam", "forall", "let"):
            sf = "(%s)" % sf

        def atome(a):
            s = _emettre_expr(a, pile)
            return "(%s)" % s if a[0] not in _ATOMIQUES else s
        return sf + "".join(" " + atome(a) for a in args)
    if t in ("lam", "forall"):
        _, bn, ty, body, bi = e
        st = _emettre_expr(ty, pile)
        sb = _emettre_expr(body, pile + [bn])
        if t == "lam":
            if bi == 0:
                return "fun {%s : %s} => %s" % (bn, st, sb)
            if bi == 1:
                return "fun ⦃%s : %s⦄ => %s" % (bn, st, sb)
            if bi == 2:
                return "fun [%s : %s] => %s" % (bn, st, sb)
            return "fun (%s : %s) => %s" % (bn, st, sb)
        # `{bn : t} → b` ne parse pas en Lean 4 (le `→` ne peut pas
        # suivre `}`) — forme `∀` obligatoire pour les lieurs non-explicites.
        if bi == 0:
            return "∀ {%s : %s}, %s" % (bn, st, sb)
        if bi == 1:
            return "∀ ⦃%s : %s⦄, %s" % (bn, st, sb)
        if bi == 2:
            return "∀ [%s : %s], %s" % (bn, st, sb)
        return "(%s : %s) → %s" % (bn, st, sb)
    if t == "let":
        _, bn, ty, v, body, _nd = e
        st = _emettre_expr(ty, pile)
        sv = _emettre_expr(v, pile)
        sb = _emettre_expr(body, pile + [bn])
        return "let %s : %s := %s; %s" % (bn, st, sv, sb)
    if t == "proj":
        _, _sn, i, tgt = e
        st = _emettre_expr(tgt, pile)
        if tgt[0] not in _ATOMIQUES:
            st = "(%s)" % st
        return "%s.%d" % (st, i + 1)
    raise GenerationError("émission non couverte : %r" % (t,))


def _emettre_decl(d):
    lps = _level_params(d)
    suf = (".{%s}" % ", ".join(lps)) if lps else ""
    k = d["kind"]
    if k == "inductive":
        inds = d["inductifs"]
        blocs = []
        for inm, ity, ctors in inds:
            cs = "\n".join("  | %s : %s" % (cnm.split(".")[-1],
                                            _emettre_expr(cty, []))
                           for cnm, cty in ctors)
            blocs.append("inductive %s : %s where\n%s"
                         % (inm, _emettre_expr(ity, []), cs))
        if len(blocs) == 1:
            return blocs[0]
        return "mutual\n" + "\nwith\n".join(
            "  " + b.replace("\n", "\n  ") for b in blocs) + "\nend"
    if k == "axiom":
        return "axiom %s%s : %s" % (d["nom"], suf,
                                    _emettre_expr(d["type"], []))
    if d["value"] is None:
        raise GenerationError("%s sans valeur : %s" % (k, d["nom"]))
    mot = {"def": "def", "theorem": "theorem", "opaque": "opaque"}[k]
    return "%s %s%s : %s := %s" % (mot, d["nom"], suf,
                                  _emettre_expr(d["type"], []),
                                  _emettre_expr(d["value"], []))


def generer(analyse):
    lignes = []
    lignes.append("/-")
    lignes.append("Module généré par generer_lean.py — NE PAS ÉDITER À LA MAIN.")
    lignes.append("  Source  : %s" % analyse["source"])
    lignes.append("  sha256  : %s" % analyse["sha256"])
    lignes.append("  n_decls : %d (toolchain %s)" % (analyse["n_decls"],
                                                    analyse["toolchain"]))
    lignes.append("  Ordre   : tri topologique du graphe de dépendances.")
    lignes.append("-/")
    lignes.append("")
    for d in _tri_topologique(analyse["decls"]):
        lignes.append(_emettre_decl(d))
    lignes.append("")
    return "\n".join(lignes) + "\n"


def main(argv):
    if len(argv) != 4 or argv[2] != "-o":
        print("usage: generer_lean.py <fichier.phiast> -o <module.lean>")
        return 2
    try:
        analyse = _parse_fichier(argv[1])
        code = generer(analyse)
    except GenerationError as e:
        print("GÉNÉRATION REFUSÉE : %s" % e, file=sys.stderr)
        return 1
    with open(argv[3], "w", encoding="utf-8") as f:
        f.write(code)
    print("généré : %s (%d octets, %d decl(s), ordre topo OK)"
          % (argv[3], len(code), analyse["n_decls"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
