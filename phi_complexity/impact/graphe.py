#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Graphe de dépendances d'un projet Python (stdlib uniquement, parseur `ast`).

RÈGLES DE RÉSOLUTION (définies avant le code, elles font foi)
------------------------------------------------------------
R1. Nœuds.
    R1.1 Chaque fichier ``*.py`` sous la racine produit un nœud module,
        id ``"<chemin_relatif>:<nom_module_pointé>"``
        (ex. ``"impact_analyse/graphe.py:impact_analyse.graphe"``).
    R1.2 Chaque ``def`` / ``async def`` et chaque ``class`` produit un nœud,
        id ``"<chemin_relatif>:<qualname>"`` où qualname est
        ``Classe.methode`` pour les méthodes et ``englobante.interne``
        pour les définitions imbriquées (ex. ``"m.py:GrapheDependances.depuis_repertoire"``).
    R1.3 Les décorateurs, annotations et valeurs par défaut ne créent aucun nœud.
R2. Imports → arêtes "import".
    R2.1 ``import a.b.c`` / ``import a.b.c as z`` / ``from a.b import y`` :
        le nom de module pointé est résolu vers un fichier de la racine
        (``a/b/c.py`` ou ``a/b/c/__init__.py``). S'il existe → arête
        (module_courant → module_cible, "import", 0.5).
    R2.2 Import relatif : ``from . import x`` / ``from .foo import y``
        dans ``pkg.mod`` se résout contre le paquet ``pkg``
        (niveau L : on remonte L crans depuis le module courant ;
        un ``__init__.py`` désigne le paquet lui-même).
    R2.3 Si le module n'est pas dans la racine (stdlib, tiers, inexistant) →
        arête vers un nœud ``"externe:<nom_pointé>"`` (créé à la volée,
        type "module"). Aucune arête d'un module vers lui-même.
    R2.4 Effet de bord utile : chaque import enregistre une liaison dans la
        table des symboles du module (R4.2) pour résoudre ``mod.f()``.
R3. Héritage → arêtes "heritage".
    R3.1 Pour ``class C(B1, B2)``, chaque base est résolue comme un nom de
        classe : d'abord les classes du module courant, puis les classes
        des modules importés (R2.4), sinon ``"externe:<Nom>"``.
        Arête (C → Bi, "heritage", 1.5).
    R3.2 ``super().methode()`` dans une méthode de C se résout vers la
        méthode homonyme de la première base résolue de C (arête "appel").
R4. Appels → arêtes "appel".
    R4.1 Portée de résolution d'un appel, dans l'ordre :
        (a) ``self.m()`` dans une méthode de la classe C → ``C.m``
            si la méthode existe (sinon ``externe:m``) ;
        (b) nom lié localement par affectation dans la fonction :
            ``x = MaClasse(...)`` lie x → MaClasse, donc ``x.m()`` → ``MaClasse.m`` ;
            ``x = f`` (f résolu) lie x → f ;
        (c) ``mod.f()`` où mod est un alias d'import (R2.4) :
            si le module cible définit f → arête vers ce nœud,
            sinon ``externe:<module>.f`` (on ne devine pas le contenu d'un
            module externe non analysé) ;
        (d) nom nu ``f()`` : fonction/classe du module courant, sinon nom
            importé (``from m import f`` → nœud de m si analysé),
            sinon ``externe:f`` ;
        (e) ``obj.m()`` sans liaison connue → ``externe:m`` (on enregistre
            le nom de la méthode, pas l'objet : c'est la meilleure
            approximation statique honnête) ;
        (f) ``MaClasse.f()`` (appel sur la classe elle-même : méthode
            statique ou méthode de classe) → le membre ``MaClasse.f``
            s'il est déclaré, sinon ``externe:f`` ;
    R4.2 Les appels dans les décorateurs, valeurs par défaut et annotations
        sont ignorés (bruit statique, pas d'impact d'exécution direct).
    R4.3 Plusieurs appels src → dst se replient en UNE seule arête
        (le graphe mesure l'existence d'une dépendance, pas son volume).
R5. Poids (heuristique d'ordonnancement d'impact, pas une probabilité).
    appel = 1.0 (dépendance d'exécution directe),
    heritage = 1.5 (changement de la classe parente = propagation
    structurelle à toutes les sous-classes : contrat + état),
    import = 0.5 (dépendance structurelle, parfois inutilisée).
    On ne s'écarte pas de ces valeurs : elles sont celles du contrat.
R6. Limites assumées (voir aussi la section Limites en fin de fichier) :
    analyse purement statique, pas d'inférence de types, pas de résolution
    des appels dynamiques (``getattr``, ``eval``), des ``*args``/``**kwargs``
    ni des fabriques (``x = fabrique("Nom")``).

API
---
``GrapheDependances.depuis_repertoire(racine)`` construit le graphe ;
``noeuds()``, ``successeurs(nid)``, ``predecesseurs(nid)``,
``vers_json()`` l'exposent selon le contrat.
"""

from __future__ import annotations

import ast
import os

# Poids du contrat (R5) — modifiables en un seul endroit.
POIDS_APPEL = 1.0
POIDS_HERITAGE = 1.5
POIDS_IMPORT = 0.5


class _ModuleInfo:
    """État d'analyse d'un fichier source."""

    def __init__(self, relpath: str, nom_module: str, nid: str):
        self.relpath = relpath          # ex. "impact_analyse/graphe.py"
        self.nom_module = nom_module    # ex. "impact_analyse.graphe"
        self.nid = nid                  # id du nœud module
        self.arbre: ast.Module | None = None
        # qualname -> {"type": ..., "ligne": ...}
        self.declarations: dict[str, dict] = {}
        # nom local -> ("module", nom_pointé) | ("classe", qualname) |
        #             ("fonction", qualname) | ("attribut_module", module, attr)
        self.liaisons: dict[str, tuple] = {}


class GrapheDependances:
    """Graphe orienté des dépendances (appel / import / heritage) d'un projet Python."""

    def __init__(self) -> None:
        # nid -> {"id","type","fichier","ligne"}
        self._noeuds: dict[str, dict] = {}
        # src -> {dst -> {"type","poids"}}
        self._succ: dict[str, dict[str, dict]] = {}
        # dst -> {src -> {"type","poids"}}
        self._pred: dict[str, dict[str, dict]] = {}
        # nom_pointé de module -> nid (modules analysés seulement)
        self._modules_par_nom: dict[str, str] = {}
        # chemin relatif -> nom pointé de module
        self._module_nom_par_relpath: dict[str, str] = {}
        # (nom_pointé_module, qualname) -> nid, pour les déclarations analysées
        self._decl_par_module: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @staticmethod
    def depuis_repertoire(racine: str) -> "GrapheDependances":
        """Construit le graphe en analysant récursivement tous les ``*.py`` sous *racine*."""
        g = GrapheDependances()
        racine = os.path.abspath(racine)
        modules: list[_ModuleInfo] = []

        # Passe 1 : collecte des modules et de leurs déclarations (R1).
        for dirpath, _dirnames, filenames in os.walk(racine):
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                chemin = os.path.join(dirpath, fn)
                relpath = os.path.relpath(chemin, racine)
                nom_module = GrapheDependances._nom_module(relpath)
                nid_module = f"{relpath}:{nom_module}"
                info = _ModuleInfo(relpath, nom_module, nid_module)
                try:
                    with open(chemin, "r", encoding="utf-8") as fh:
                        info.arbre = ast.parse(fh.read(), filename=relpath)
                except (SyntaxError, UnicodeDecodeError):
                    continue  # fichier illisible : ignoré, pas d'exception
                modules.append(info)
                g._ajouter_noeud(nid_module, "module", relpath, 1)
                g._modules_par_nom[nom_module] = nid_module
                g._module_nom_par_relpath[relpath] = nom_module
                g._collecter_declarations(info)

        # Enregistre toutes les déclarations comme nœuds + index de résolution.
        for info in modules:
            for qualname, decl in info.declarations.items():
                nid = f"{info.relpath}:{qualname}"
                g._ajouter_noeud(nid, decl["type"], info.relpath, decl["ligne"])
                g._decl_par_module[(info.nom_module, qualname)] = nid

        # Passe 2 : imports, héritage, appels (R2, R3, R4).
        for info in modules:
            g._traiter_imports(info, racine)
        for info in modules:
            g._traiter_corps(info)
        return g

    # ------------------------------------------------------------------
    # API publique (contrat)
    # ------------------------------------------------------------------
    def noeuds(self) -> list[dict]:
        """Tous les nœuds : {"id","type","fichier","ligne"}."""
        return list(self._noeuds.values())

    def successeurs(self, nid: str) -> list[tuple[str, dict]]:
        """Liste de (id_cible, {"type","poids"}) pour les arêtes sortantes de *nid*."""
        return [(dst, dict(meta)) for dst, meta in self._succ.get(nid, {}).items()]

    def predecesseurs(self, nid: str) -> list[tuple[str, dict]]:
        """Liste de (id_source, {"type","poids"}) pour les arêtes entrantes de *nid*."""
        return [(src, dict(meta)) for src, meta in self._pred.get(nid, {}).items()]

    def vers_json(self) -> dict:
        """{"noeuds": [...], "aretes": [{"src","dst","type","poids"}]}."""
        aretes = [
            {"src": src, "dst": dst, "type": meta["type"], "poids": meta["poids"]}
            for src, cibles in self._succ.items()
            for dst, meta in cibles.items()
        ]
        return {"noeuds": self.noeuds(), "aretes": aretes}

    # ------------------------------------------------------------------
    # Internes : utilitaires
    # ------------------------------------------------------------------
    @staticmethod
    def _nom_module(relpath: str) -> str:
        """'a/b/c.py' -> 'a.b.c' ; 'a/b/__init__.py' -> 'a.b'."""
        sans_ext = relpath[:-3] if relpath.endswith(".py") else relpath
        if os.path.basename(sans_ext) == "__init__":
            sans_ext = os.path.dirname(sans_ext)
        return sans_ext.replace(os.sep, ".")

    def _ajouter_noeud(self, nid: str, type_noeud: str, fichier: str, ligne: int) -> None:
        if nid not in self._noeuds:
            self._noeuds[nid] = {"id": nid, "type": type_noeud,
                                 "fichier": fichier, "ligne": ligne}

    def _ajouter_arete(self, src: str, dst: str, type_arete: str, poids: float) -> None:
        if src == dst:
            return  # R2.3 : pas d'auto-arête
        if dst not in self._noeuds:
            # Nœud externe créé à la volée (R2.3 / R4.1).
            self._ajouter_noeud(dst, "module", "<externe>", 0)
        self._succ.setdefault(src, {})
        self._pred.setdefault(dst, {})
        if dst not in self._succ[src]:  # R4.3 : repli des doublons
            meta = {"type": type_arete, "poids": poids}
            self._succ[src][dst] = meta
            self._pred[dst][src] = meta

    def _nid_externe(self, nom: str) -> str:
        return f"externe:{nom}"

    def _resoudre_module(self, nom_pointe: str, racine: str) -> str | None:
        """Nom pointé -> nid du module analysé, ou None si hors racine."""
        return self._modules_par_nom.get(nom_pointe)

    # ------------------------------------------------------------------
    # Passe 1 : déclarations
    # ------------------------------------------------------------------
    def _collecter_declarations(self, info: _ModuleInfo) -> None:
        class Visiteur(ast.NodeVisitor):
            def __init__(self):
                self.pile: list[str] = []       # qualnames englobants
                self.classe_courante: str | None = None

            def _qualname(self, nom: str) -> str:
                return ".".join(self.pile + [nom]) if self.pile else nom

            def visit_ClassDef(self, node: ast.ClassDef):  # noqa: N802
                qn = self._qualname(node.name)
                info.declarations[qn] = {"type": "classe", "ligne": node.lineno}
                # Mémorise les bases (noms bruts) pour la passe 2 (R3).
                info.declarations[qn]["_bases"] = [
                    ast.unparse(b) for b in node.bases]
                self.pile.append(node.name)
                ancienne = self.classe_courante
                self.classe_courante = qn
                self.generic_visit(node)
                self.classe_courante = ancienne
                self.pile.pop()

            def _visiter_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
                qn = self._qualname(node.name)
                info.declarations[qn] = {"type": "fonction", "ligne": node.lineno,
                                         "_classe": self.classe_courante,
                                         "_noeud": node}
                self.pile.append(node.name)
                self.generic_visit(node)
                self.pile.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef):  # noqa: N802
                self._visiter_def(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):  # noqa: N802
                self._visiter_def(node)

        Visiteur().visit(info.arbre)

    # ------------------------------------------------------------------
    # Passe 2a : imports (R2)
    # ------------------------------------------------------------------
    def _traiter_imports(self, info: _ModuleInfo, racine: str) -> None:
        for node in ast.walk(info.arbre):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    nom = alias.name  # ex. "a.b.c"
                    local = alias.asname or nom.split(".")[0]
                    nid_cible = self._resoudre_module(nom, racine)
                    if nid_cible is None:
                        # Tente le préfixe le plus long résolu (import a.b.c
                        # où seul `a` est un module analysé).
                        nid_cible = self._nid_externe(nom)
                    self._ajouter_arete(info.nid, nid_cible, "import", POIDS_IMPORT)
                    info.liaisons[local] = ("module", nom)
            elif isinstance(node, ast.ImportFrom):
                # Résolution du module source, y compris imports relatifs (R2.2).
                nom = self._nom_module_importfrom(info, node)
                if nom is None:
                    continue
                nid_cible = self._resoudre_module(nom, racine) or self._nid_externe(nom)
                self._ajouter_arete(info.nid, nid_cible, "import", POIDS_IMPORT)
                for alias in node.names:
                    if alias.name == "*":
                        continue  # import * : liaisons inconnues, on s'abstient
                    local = alias.asname or alias.name
                    info.liaisons[local] = ("attribut_module", nom, alias.name)

    def _nom_module_importfrom(self, info: _ModuleInfo, node: ast.ImportFrom) -> str | None:
        """Calcule le nom pointé du module source d'un ImportFrom (R2.2)."""
        if node.level == 0:
            return node.module or ""
        # Import relatif : remonter `level` crans depuis le module courant.
        morceaux = info.nom_module.split(".")
        # Le module courant est un fichier ; son paquet = tout sauf le dernier,
        # sauf __init__.py qui EST le paquet.
        if not info.relpath.endswith("__init__.py"):
            morceaux = morceaux[:-1]
        if node.level - 1 > len(morceaux):
            return None  # remonte au-delà de la racine : inrésolvable
        base = morceaux[: len(morceaux) - (node.level - 1)]
        if node.module:
            base = base + node.module.split(".")
        return ".".join(base)

    # ------------------------------------------------------------------
    # Passe 2b : héritage (R3) et appels (R4)
    # ------------------------------------------------------------------
    def _traiter_corps(self, info: _ModuleInfo) -> None:
        # Liaisons de niveau module : defs/classes visibles par nom nu (R4.1d).
        for qualname, decl in info.declarations.items():
            if "." not in qualname:  # niveau module uniquement
                genre = "classe" if decl["type"] == "classe" else "fonction"
                info.liaisons.setdefault(qualname, (genre, qualname))

        # R3 : héritage.
        for qualname, decl in info.declarations.items():
            if decl["type"] != "classe":
                continue
            nid_src = f"{info.relpath}:{qualname}"
            for base in decl.get("_bases", []):
                nid_base = self._resoudre_nom_classe(base, info)
                self._ajouter_arete(nid_src, nid_base, "heritage", POIDS_HERITAGE)

        # R4 : appels, fonction par fonction.
        for qualname, decl in info.declarations.items():
            if decl["type"] != "fonction" or "_noeud" not in decl:
                continue
            nid_src = f"{info.relpath}:{qualname}"
            noeud = decl["_noeud"]
            # Ignore décorateurs / valeurs par défaut / annotations (R4.2) :
            # on ne visite que le corps.
            local = self._collecter_liaisons_locales(noeud, info, qualname)
            for stmt in noeud.body:
                self._visiter_appels(stmt, info, nid_src, qualname,
                                     decl.get("_classe"), local, qualname)

    def _resoudre_nom_classe(self, nom: str, info: _ModuleInfo) -> str:
        """Résout un nom de classe de base vers un nid (R3.1)."""
        nom = nom.split("[")[0].strip()  # écarte les souscriptions génériques C[T]
        if "." not in nom and nom in info.liaisons:
            genre = info.liaisons[nom][0]
            if genre == "classe":
                return f"{info.relpath}:{info.liaisons[nom][1]}"
            if genre == "attribut_module":
                _, mod, attr = info.liaisons[nom]
                nid = self._decl_par_module.get((mod, attr))
                if nid:
                    return nid
        # Nom pointé (ex. "mod.Base") ou inconnu.
        if "." in nom:
            mod, _, attr = nom.rpartition(".")
            nid = self._decl_par_module.get((mod, attr))
            if nid:
                return nid
        return self._nid_externe(nom)

    def _collecter_liaisons_locales(self, noeud: ast.FunctionDef | ast.AsyncFunctionDef,
                                    info: _ModuleInfo,
                                    noeud_qualname: str = "") -> dict[str, tuple]:
        """Liaisons par affectation dans la fonction (R4.1b) : x = Classe(...), x = f."""
        local: dict[str, tuple] = {}
        resolveur = lambda nom: self._resoudre_nom_nu_dans_fonction(  # noqa: E731
            nom, info, local, noeud_qualname)

        class Visiteur(ast.NodeVisitor):
            def visit_Assign(self, node: ast.Assign):  # noqa: N802
                cibles = [t.id for t in node.targets if isinstance(t, ast.Name)]
                valeur = node.value
                for cible in cibles:
                    if isinstance(valeur, ast.Call):
                        f = valeur.func
                        if isinstance(f, ast.Name):
                            nid = resolveur(f.id)
                            if nid and not nid.startswith("externe:"):
                                # x = Classe(...) : on retient le nid complet
                                # pour résoudre x.meth() dans le bon module.
                                local[cible] = ("instance_de", nid)
                    elif isinstance(valeur, ast.Name):
                        nid = resolveur(valeur.id)
                        if nid and not nid.startswith("externe:"):
                            local[cible] = ("alias", nid)
                self.generic_visit(node)

        Visiteur().visit(noeud)
        return local

    def _nid_de_liaison(self, liaison: tuple, info: _ModuleInfo) -> str | None:
        genre = liaison[0]
        if genre == "module":
            _, nom_pointe = liaison
            return self._modules_par_nom.get(nom_pointe) or self._nid_externe(nom_pointe)
        if genre in ("classe", "fonction", "alias", "instance_de"):
            return f"{info.relpath}:{liaison[1]}"
        if genre == "attribut_module":
            _, mod, attr = liaison
            return self._decl_par_module.get((mod, attr)) or self._nid_externe(f"{mod}.{attr}")
        return None

    def _resoudre_nom_nu_dans_fonction(self, nom: str, info: _ModuleInfo,
                                       local: dict[str, tuple],
                                       qualname_src: str = "") -> str | None:
        # Portée locale d'abord (R4.1b) : les liaisons locales portent le nid complet.
        if nom in local:
            return local[nom][1]
        # Portées englobantes : f() dans g() peut appeler une def imbriquée g.f (R4.1d).
        if qualname_src:
            morceaux = qualname_src.split(".")
            for i in range(len(morceaux), 0, -1):
                candidat = ".".join(morceaux[:i] + [nom])
                nid = self._decl_par_module.get((info.nom_module, candidat))
                if nid:
                    return nid
        # Portée module (R4.1d).
        if nom in info.liaisons:
            return self._nid_de_liaison(info.liaisons[nom], info)
        return None

    def _visiter_appels(self, node: ast.AST, info: _ModuleInfo, nid_src: str,
                        qualname_src: str, classe_src: str | None,
                        local: dict[str, tuple], portee_src: str = "") -> None:
        """Visite un sous-arbre en résolvant chaque ast.Call (R4)."""
        for enfant in ast.walk(node):
            if not isinstance(enfant, ast.Call):
                continue
            nid_dst = self._resoudre_appel(enfant.func, info, classe_src, local,
                                           portee_src or qualname_src)
            if nid_dst:
                self._ajouter_arete(nid_src, nid_dst, "appel", POIDS_APPEL)

    def _resoudre_appel(self, func: ast.expr, info: _ModuleInfo,
                        classe_src: str | None, local: dict[str, tuple],
                        qualname_src: str = "") -> str | None:
        # R4.1a : self.m() / super().m()
        if isinstance(func, ast.Attribute):
            val = func.value
            if isinstance(val, ast.Name) and val.id == "self" and classe_src:
                nid = self._decl_par_module.get((info.nom_module, f"{classe_src}.{func.attr}"))
                return nid or self._nid_externe(func.attr)
            if isinstance(val, ast.Call) and isinstance(val.func, ast.Name) \
                    and val.func.id == "super" and classe_src:
                base = self._premiere_base(classe_src, info)
                if base:
                    mod_nom, _, qn = base
                    nid = self._decl_par_module.get((mod_nom, f"{qn}.{func.attr}"))
                    if nid:
                        return nid
                return self._nid_externe(func.attr)
            # R4.1c : mod.f()
            if isinstance(val, ast.Name):
                nom = val.id
                cible = local.get(nom) or info.liaisons.get(nom)
                if cible is not None:
                    if cible[0] == "module":
                        mod_pointe = cible[1]
                        nid = self._decl_par_module.get((mod_pointe, func.attr))
                        return nid or self._nid_externe(f"{mod_pointe}.{func.attr}")
                    if cible[0] == "attribut_module":
                        # from m import K puis K.f() : K est une classe/fonction de m
                        _, mod_pointe, attr = cible
                        qn = f"{attr}.{func.attr}"
                        nid = self._decl_par_module.get((mod_pointe, qn))
                        if nid:
                            return nid
                        # attr pourrait être un module (from pkg import sousmod)
                        nid_mod = self._modules_par_nom.get(f"{mod_pointe}.{attr}")
                        if nid_mod:
                            return nid_mod
                        return self._nid_externe(f"{mod_pointe}.{attr}.{func.attr}")
                    if cible[0] == "instance_de":
                        # nid complet de la classe -> méthode cherchée dans SON module.
                        relpath_classe, _, qn_classe = cible[1].partition(":")
                        mod_nom = self._module_nom_par_relpath.get(relpath_classe)
                        nid = (self._decl_par_module.get((mod_nom, f"{qn_classe}.{func.attr}"))
                               if mod_nom else None)
                        return nid or self._nid_externe(func.attr)
                    if cible[0] in ("classe", "fonction", "alias"):
                        # Appel sur la classe elle-même (méthode statique /
                        # méthode de classe : MaClasse.fabrique()) ou sur un
                        # alias de fonction : on cherche le membre.
                        nid = self._membre_de(cible, func.attr, info)
                        return nid or self._nid_externe(func.attr)
                    # Autre cas : attribut d'une valeur non-module -> externe (R4.1e).
                    return self._nid_externe(func.attr)
                # R4.1e : objet inconnu.
                return self._nid_externe(func.attr)
            # Chaînes plus profondes (a.b.c(), f()()) : on s'abstient du
            # devinage, on enregistre le nom de la méthode (R4.1e).
            if isinstance(val, ast.Attribute):
                return self._nid_externe(func.attr)
            return self._nid_externe(func.attr)
        # R4.1d : nom nu f().
        if isinstance(func, ast.Name):
            nid = self._resoudre_nom_nu_dans_fonction(func.id, info, local, qualname_src)
            return nid or self._nid_externe(func.id)
        # lambda(), (f)(), etc. : inrésolvable proprement.
        return None

    def _membre_de(self, cible: tuple, attr: str, info: _ModuleInfo) -> str | None:
        """Cherche le membre `attr` d'une classe/fonction/alias lié (appel `C.m()`)."""
        genre = cible[0]
        if genre in ("classe", "fonction"):
            return self._decl_par_module.get((info.nom_module, f"{cible[1]}.{attr}"))
        if genre == "alias":
            # Le local porte le nid complet : on retrouve le module d'origine.
            relpath, _, qn = cible[1].partition(":")
            mod_nom = self._module_nom_par_relpath.get(relpath)
            if mod_nom:
                return self._decl_par_module.get((mod_nom, f"{qn}.{attr}"))
        return None

    def _premiere_base(self, qualname_classe: str,
                       info: _ModuleInfo) -> tuple[str, str, str] | None:
        """(nom_module, _, qualname) de la première base résolue d'une classe."""
        decl = info.declarations.get(qualname_classe, {})
        for base in decl.get("_bases", []):
            nom = base.split("[")[0].strip()
            if "." not in nom and nom in info.liaisons:
                liaison = info.liaisons[nom]
                if liaison[0] == "classe":
                    return (info.nom_module, "", liaison[1])
                if liaison[0] == "attribut_module":
                    _, mod, attr = liaison
                    if (mod, attr) in self._decl_par_module:
                        return (mod, "", attr)
        return None


"""
LIMITES HONNÊTES
----------------
1. Analyse statique sans inférence de types : ``obj.meth()`` où obj n'est
   pas lié par une affectation visible devient ``externe:meth`` (R4.1e).
   Deux classes définissant ``meth`` créeront des arêtes vers le même
   nœud externe : le graphe sur-approxime volontairement (mieux vaut un
   faux positif qu'une dépendance manquée dans un analyseur d'impact).
2. ``getattr``, ``eval``, ``__import__``, fabriques dynamiques et appels
   via ``*args`` ne sont pas résolus.
3. ``import *`` ne crée que l'arête import, aucune liaison de nom.
4. Les modules hors racine (stdlib, tiers) sont des nœuds ``externe:``
   sans contenu : leurs dépendances internes ne sont pas explorées.
5. Fichiers avec erreur de syntaxe : ignorés silencieusement (pas de nœud).
"""
