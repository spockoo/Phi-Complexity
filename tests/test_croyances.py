"""
tests/test_croyances.py — Tests de la carte croyante (v0.10.0 « Les Chemins Croyants »).

Couvre : formule exacte des termes, dégradation neutre hors git,
graphe d'imports (chaîne a→b→c), repli grep du capteur sorry
(hors commentaires, blocs /- -/), et le CLI `phi chemins --format json`.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import textwrap

import pytest

from phi_complexity.croyances import (
    POIDS_CROYANCES,
    _churn_git,
    _dependants_aval,
    _graphe_imports,
    _nettoyer_lean,
    _posterior,
    _sorry_grep_repli,
    _sorry_par_symbole,
    chemins_croyants,
)
from phi_complexity.editeur.indexeur import Symbole
from phi_complexity.core import VERSION


V100 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ecrire(dossier: str, nom: str, contenu: str) -> str:
    chemin = os.path.join(dossier, nom)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(contenu))
    return chemin


# ────────────────────────────────────────────────────────
# 1. FORMULE — termes exacts sur cas synthétique
# ────────────────────────────────────────────────────────

class TestFormule:
    def test_termes_exacts(self):
        """Chaque terme vaut exactement sa définition ; le postérieur est leur somme."""
        post, termes = _posterior(complexite=99, sorry=True, dependants=3, churn=7)
        assert termes["prior"] == round(math.log1p(99), 4)
        assert termes["sorry"] == POIDS_CROYANCES["sorry"] == 3.0
        assert termes["aval"] == round(POIDS_CROYANCES["aval"] * math.log1p(3), 4)
        assert termes["churn"] == round(POIDS_CROYANCES["churn"] * math.log1p(7), 4)
        assert termes["tests"] == 0.0
        assert post == round(sum(termes.values()), 4)

    def test_sans_evidence(self):
        """Sans sorry, sans dépendants, sans churn : postérieur = prior seul."""
        post, termes = _posterior(complexite=15, sorry=False, dependants=0, churn=0)
        assert termes["sorry"] == 0.0
        assert termes["aval"] == 0.0
        assert termes["churn"] == 0.0
        assert post == termes["prior"] == round(math.log1p(15), 4)

    def test_poids_en_un_seul_endroit(self):
        """Les poids utilisés sont exactement POIDS_CROYANCES (pas de copie)."""
        assert set(POIDS_CROYANCES) == {"sorry", "aval", "churn", "tests"}


# ────────────────────────────────────────────────────────
# 2. DÉGRADATION NEUTRE — pas de git → churn neutre et signalé
# ────────────────────────────────────────────────────────

class TestDegradation:
    def test_churn_hors_depot(self):
        """Hors dépôt git : {} — jamais de churn inventé."""
        with tempfile.TemporaryDirectory() as d:
            assert _churn_git(os.path.abspath(d)) == {}

    def test_churn_neutre_signale(self):
        """Sans git : termes churn à 0 et capteur absent de capteurs_actifs."""
        with tempfile.TemporaryDirectory() as d:
            _ecrire(d, "m.py", "def f():\n    return 1\n")
            carte = chemins_croyants(d)
            assert carte["nb_symboles"] == 1
            assert carte["symboles"][0]["termes"]["churn"] == 0.0
            assert not any(c.startswith("churn_git") for c in carte["capteurs_actifs"])
            assert carte["capteur_degrade"] is False  # .py : sorry non applicable

    def test_limites_exhibees(self):
        """Le disclaimer d'honnêteté est présent dans la sortie."""
        with tempfile.TemporaryDirectory() as d:
            _ecrire(d, "m.py", "def f():\n    return 1\n")
            carte = chemins_croyants(d)
            assert "HEURISTIQUE" in carte["limites"]
            assert "PAS une probabilité" in carte["limites"]
            assert "formule" in carte and "poids" in carte


# ────────────────────────────────────────────────────────
# 3. GRAPHE D'IMPORTS — chaîne a→b→c
# ────────────────────────────────────────────────────────

class TestGrapheImports:
    def _projet_chaine(self, d):
        # Corps IDENTIQUES : les priors sont égaux, seule la centralité
        # (terme aval) distingue les postérieurs.
        _ecrire(d, "c.py", "def f_c():\n    return 1\n")
        _ecrire(d, "b.py", "import c\ndef f_b():\n    return 1\n")
        _ecrire(d, "a.py", "import b\ndef f_a():\n    return 1\n")

    def test_dependants_transitifs(self):
        """a importe b, b importe c : dépendants(a)=0, (b)=1, (c)=2."""
        with tempfile.TemporaryDirectory() as d:
            self._projet_chaine(d)
            carte = chemins_croyants(d)
            par_nom = {s["nom"]: s for s in carte["symboles"]}
            assert par_nom["f_a"]["dependants_aval"] == 0
            assert par_nom["f_b"]["dependants_aval"] == 1
            assert par_nom["f_c"]["dependants_aval"] == 2

    def test_blast_radius(self):
        """Le rayon d'explosion de c contient b et a (noms relatifs)."""
        with tempfile.TemporaryDirectory() as d:
            self._projet_chaine(d)
            graphe, _ = _graphe_imports(os.path.abspath(d),
                                        [os.path.join(d, n) for n in ("a.py", "b.py", "c.py")])
            c_canon = os.path.normpath(os.path.join(d, "c.py"))
            deps = _dependants_aval(graphe, c_canon)
            noms = {os.path.basename(p) for p in deps}
            assert noms == {"a.py", "b.py"}

    def test_posterior_respecte_centralite(self):
        """À complexité égale, le plus central a le plus grand postérieur."""
        with tempfile.TemporaryDirectory() as d:
            self._projet_chaine(d)
            carte = chemins_croyants(d)
            par_nom = {s["nom"]: s for s in carte["symboles"]}
            assert par_nom["f_c"]["posterior"] > par_nom["f_b"]["posterior"]
            assert par_nom["f_b"]["posterior"] > par_nom["f_a"]["posterior"]


# ────────────────────────────────────────────────────────
# 4. CAPTEUR SORRY — repli grep hors commentaires
# ────────────────────────────────────────────────────────

LEAN_TROU = """\
theorem avec_trou : True := by
  sorry
-- un sorry en commentaire ne compte pas
/-
bloc de commentaire
avec sorry dedans
-/
theorem plein : True := trivial
"""

def _symboles_lean(chemin):
    return [
        Symbole(nom="avec_trou", ligne=1, complexite=5, langage="lean", fichier=chemin),
        Symbole(nom="plein", ligne=7, complexite=3, langage="lean", fichier=chemin),
    ]


class TestSorryGrep:
    def test_commentaires_et_chaines_retires(self):
        propres = _nettoyer_lean(LEAN_TROU.splitlines())
        assert propres[2] == ""          # -- commentaire
        assert propres[3] == ""         # /- ...
        assert propres[4] == ""         # bloc
        assert propres[5] == ""         # -/
        assert "sorry" in propres[1]    # le vrai, ligne 2

    def test_sorry_dans_chaine_ignore(self):
        """Un `sorry` dans un littéral de chaîne ne compte pas (faux positif tué)."""
        contenu = ('def diagnostic : String :=\n'
                   '  "statut des 6 sorrys du Master"\n'
                   'theorem vrai : True := trivial\n')
        with tempfile.TemporaryDirectory() as d:
            chemin = _ecrire(d, "t.lean", contenu)
            syms = [Symbole(nom="diagnostic", ligne=1, complexite=2,
                            langage="lean", fichier=chemin),
                    Symbole(nom="vrai", ligne=3, complexite=2,
                            langage="lean", fichier=chemin)]
            res = _sorry_grep_repli(chemin, syms)
            assert res[("diagnostic", 1)] is False
            assert res[("vrai", 3)] is False

    def test_string_gap_multiligne(self):
        """Le `\\` de fin de ligne prolonge la chaîne (string gap Lean)."""
        lignes = ['  "debut \\', '   milieu sorry fin",', 'theorem x : True := trivial']
        propres = _nettoyer_lean(lignes)
        assert propres[0] == "  "
        assert propres[1] == ","      # la virgule hors chaîne subsiste…
        assert "sorry" not in propres[1]  # …mais le sorry dans la chaîne est parti
        assert "theorem" in propres[2]
        contenu = ('def diagnostic : String :=\n'
                   '  "statut des 6 sorrys du Master"\n'
                   'theorem vrai : True := trivial\n')
        with tempfile.TemporaryDirectory() as d:
            chemin = _ecrire(d, "t.lean", contenu)
            syms = [Symbole(nom="diagnostic", ligne=1, complexite=2,
                            langage="lean", fichier=chemin),
                    Symbole(nom="vrai", ligne=3, complexite=2,
                            langage="lean", fichier=chemin)]
            res = _sorry_grep_repli(chemin, syms)
            assert res[("diagnostic", 1)] is False
            assert res[("vrai", 3)] is False

    def test_grep_repli(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = _ecrire(d, "t.lean", LEAN_TROU)
            res = _sorry_grep_repli(chemin, _symboles_lean(chemin))
            assert res[("avec_trou", 1)] is True
            assert res[("plein", 7)] is False

    def test_sorry_non_applicable_python(self):
        """En Python, `sorry` n'existe pas : capteur non applicable, pas dégradé."""
        with tempfile.TemporaryDirectory() as d:
            chemin = _ecrire(d, "m.py", "def f():\n    return 1  # sorry\n")
            syms = [Symbole(nom="f", ligne=1, complexite=2, langage="python", fichier=chemin)]
            res, mode = _sorry_par_symbole(chemin, syms)
            assert mode == "non_applicable"
            assert res[("f", 1)] is False

    def test_mode_grep_repli_signale(self):
        """En v100 le flag natif n'existe pas : le mode est le repli, honnête."""
        with tempfile.TemporaryDirectory() as d:
            chemin = _ecrire(d, "t.lean", LEAN_TROU)
            _, mode = _sorry_par_symbole(chemin, _symboles_lean(chemin))
            assert mode == "grep_repli"


# ────────────────────────────────────────────────────────
# 5. BOUT EN BOUT — vrai fichier Lean via tree-sitter
# ────────────────────────────────────────────────────────

class TestBoutEnBoutLean:
    def test_attaquer_en_premier(self):
        """Le théorème à sorry arrive en tête de « attaquer en premier »."""
        with tempfile.TemporaryDirectory() as d:
            _ecrire(d, "t.lean", LEAN_TROU)
            carte = chemins_croyants(d)
            assert carte["capteur_degrade"] is True
            assert "sorry:grep_repli" in carte["capteurs_actifs"]
            top = carte["attaquer_en_premier"]
            assert len(top) == 1
            assert top[0]["nom"] == "avec_trou"
            assert top[0]["termes"]["sorry"] == POIDS_CROYANCES["sorry"]
            # Le symbole plein est classé mais pas dans la liste d'attaque.
            noms = [s["nom"] for s in carte["symboles"]]
            assert "plein" in noms
            assert all(t["nom"] != "plein" for t in top)


# ────────────────────────────────────────────────────────
# 6. CLI — `phi chemins --format json`
# ────────────────────────────────────────────────────────

class TestCliChemins:
    def test_json_valide(self):
        with tempfile.TemporaryDirectory() as d:
            _ecrire(d, "m.py", "def f():\n    return 1\n")
            env = dict(os.environ, PYTHONPATH=V100)
            r = subprocess.run(
                [sys.executable, "-m", "phi_complexity.cli",
                 "chemins", d, "--format", "json"],
                capture_output=True, text=True, env=env, timeout=180)
            assert r.returncode == 0, r.stderr[-500:]
            carte = json.loads(r.stdout)
            assert carte["version_phi"] == VERSION  # suit la version courante
            assert carte["nb_symboles"] == 1
            assert "attaquer_en_premier" in carte
            assert "limites" in carte

    def test_dossier_introuvable(self):
        env = dict(os.environ, PYTHONPATH=V100)
        r = subprocess.run(
            [sys.executable, "-m", "phi_complexity.cli",
             "chemins", "/tmp/phi_chemins_inexistant_xyz", "--format", "json"],
            capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 1
