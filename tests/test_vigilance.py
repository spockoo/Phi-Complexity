"""
tests/test_vigilance.py — Vigilance : détecteurs absolus (état unique).

Falsification d'abord : chaque détecteur est testé sur cas à réponse
connue. Le détecteur de vacuité DOIT flagger le cas réel BMOControl
(2026-10-08) et NE DOIT PAS flagger les hypothèses saines.

Règle constitutionnelle : aucune sortie ne porte de langage prescriptif
(vérifié contre LISTE_NOIRE_PRESCRIPTIF).
"""
import os

import pytest

from phi_complexity.vigilance import (
    _est_impossible,
    _extraire_comparaison,
    _extraire_types_hypotheses,
    _substituer,
    _tenter_depliage,
    _tester_vacuite,
    detecter_vacuite,
    detecter_hypotheses_inutilisees,
    formater_console,
    inventaire_axiomes,
    scanner_complet,
)
from phi_complexity.parseur_autonome import parse
from phi_complexity.radar import LISTE_NOIRE_PRESCRIPTIF


# ────────────────────────────────────────────────────────
# Motifs d'impossibilité : unitaires
# ────────────────────────────────────────────────────────

class TestMotifsImpossibles:
    def test_direct_lt_reflexif(self):
        assert _est_impossible("T < T") is not None

    def test_direct_gt_reflexif(self):
        assert _est_impossible("x > x") is not None

    def test_direct_neq_reflexif(self):
        assert _est_impossible("a ≠ a") is not None

    def test_false_seul(self):
        assert _est_impossible("False") is not None

    def test_numeral(self):
        assert _est_impossible("0 < 0") is not None

    def test_terme_pointu(self):
        # Le cas réel : d.Tstar < d.Tstar
        assert _est_impossible("d.Tstar < d.Tstar") is not None

    def test_sain_lt_distinct(self):
        assert _est_impossible("0 < T") is None

    def test_sain_lt_distinct_2(self):
        assert _est_impossible("T < d.Tstar") is None

    def test_sain_le_reflexif(self):
        # ≤ est réflexif : VRAI, ne pas signaler.
        assert _est_impossible("T ≤ T") is None

    def test_sain_egalite(self):
        assert _est_impossible("x = x") is None

    def test_sain_conjonction(self):
        assert _est_impossible("0 < T ∧ T < d.Tstar") is None

    def test_comparaison_extraction(self):
        g, op, d = _extraire_comparaison("d.Tstar<d.Tstar")
        assert (g, op, d) == ("d.Tstar", "<", "d.Tstar")

    def test_pas_de_confusion_le(self):
        # "a <= b" ne doit pas être lu comme "a <" + "= b".
        assert _extraire_comparaison("a<=b") is None
        assert _extraire_comparaison("a≤b") is None


# ────────────────────────────────────────────────────────
# Dépliage définitionnel
# ────────────────────────────────────────────────────────

CODE_BMOCONTROL = """\
def BMOControl (d : NSData) (T : ℝ) : Prop :=
  0 < T ∧ T < d.Tstar ∧ True

theorem usage_vicieux (d : NSData) (hH2' : BMOControl d d.Tstar) : True :=
  trivial

theorem usage_sain (d : NSData) (T : ℝ) (h : T < d.Tstar) : True :=
  trivial
"""

CODE_DIRECT = """\
theorem direct_vicieux (T : ℝ) (h : T < T) : True :=
  trivial

theorem direct_false (h : False) : True :=
  trivial

theorem sain (T : ℝ) (h1 : 0 < T) (h2 : T < 100) : True :=
  trivial
"""


class TestDepliage:
    def _carte(self):
        from phi_complexity.vigilance import _carte_definitions
        decls = parse(CODE_BMOCONTROL).declarations
        return _carte_definitions(decls)

    def test_carte_contient_def(self):
        carte = self._carte()
        assert "BMOControl" in carte
        binders, corps = carte["BMOControl"]
        assert binders == ["d", "T"]

    def test_depliage_cas_reel(self):
        # LE cas qui a motivé le détecteur : BMOControl d d.Tstar
        # doit révéler d.Tstar < d.Tstar après substitution.
        carte = self._carte()
        deplie = _tenter_depliage("BMOControl d d.Tstar", carte)
        assert deplie is not None
        assert "d.Tstar<d.Tstar" in deplie.replace(" ", "")

    def test_tester_vacuite_cas_reel(self):
        carte = self._carte()
        motif = _tester_vacuite("BMOControl d d.Tstar", carte)
        assert motif is not None
        assert "d.Tstar" in motif

    def test_tester_vacuite_sain(self):
        carte = self._carte()
        assert _tester_vacuite("BMOControl d T", carte) is None

    def test_substitution_simultanee(self):
        # Pas de re-appariement : d ne doit pas matcher dans d.Tstar.
        res = _substituer("T < d.Tstar", ["d", "T"], ["d", "d.Tstar"])
        assert res.replace(" ", "") == "d.Tstar<d.Tstar"

    def test_substitution_protege_pointes(self):
        res = _substituer("f d", ["d"], ["e"])
        assert res == "f e"
        # 'd' dans 'd.Tstar' source ne doit pas être touché si binder=d.
        res2 = _substituer("d.Tstar < T", ["T"], ["s"])
        assert res2 == "d.Tstar < s"

    def test_depliage_ambigu_ignore(self):
        # Nombre d'arguments ≠ binders → None (pas de faux positif).
        carte = self._carte()
        assert _tenter_depliage("BMOControl d", carte) is None
        assert _tenter_depliage("BMOControl d T x", carte) is None


# ────────────────────────────────────────────────────────
# Extraction des hypothèses
# ────────────────────────────────────────────────────────

class TestExtractionHypotheses:
    def test_binders_types(self):
        decls = parse(CODE_DIRECT).declarations
        thm = next(d for d in decls if d.nom == "direct_vicieux")
        from phi_complexity.vigilance import _enonce
        types = _extraire_types_hypotheses(_enonce(thm))
        assert any("T<T" in t.replace(" ", "") for t in types), types

    def test_binders_multiples(self):
        decls = parse(CODE_DIRECT).declarations
        thm = next(d for d in decls if d.nom == "sain")
        from phi_complexity.vigilance import _enonce
        types = _extraire_types_hypotheses(_enonce(thm))
        assert len(types) == 3, types  # T:ℝ, h1, h2


# ────────────────────────────────────────────────────────
# Détecteur complet : doit flagger / ne doit pas flagger
# ────────────────────────────────────────────────────────

class TestDetecterVacuite:
    @pytest.fixture
    def corpus(self, tmp_path):
        f = tmp_path / "cas.lean"
        f.write_text(CODE_BMOCONTROL + "\n" + CODE_DIRECT)
        return str(tmp_path)

    def test_flagge_cas_reel(self, corpus):
        fiches, decls = scanner_complet(corpus)
        obs = detecter_vacuite(fiches, decls)
        vicieux = [o for o in obs if o.nom == "usage_vicieux"
                   and o.kind == "vacuite_hypothese"]
        assert len(vicieux) == 1, [(o.nom, o.kind) for o in obs]

    def test_flagge_directs(self, corpus):
        fiches, decls = scanner_complet(corpus)
        obs = detecter_vacuite(fiches, decls)
        noms = {(o.nom, o.kind) for o in obs}
        assert ("direct_vicieux", "vacuite_hypothese") in noms
        assert ("direct_false", "vacuite_hypothese") in noms

    def test_ne_flagge_pas_sains(self, corpus):
        fiches, decls = scanner_complet(corpus)
        obs = detecter_vacuite(fiches, decls)
        for o in obs:
            assert o.nom not in ("usage_sain", "sain"), \
                f"faux positif sur {o.nom} : {o.apres}"

    def test_false_conclusion_legitime(self, tmp_path):
        # Preuve par contradiction : False en CONCLUSION est légitime.
        code = """theorem contrapositive (h1 : P) (h2 : ¬ P) : False :=
  absurd h1 h2
"""
        f = tmp_path / "cas.lean"
        f.write_text(code)
        fiches, decls = scanner_complet(str(tmp_path))
        obs = detecter_vacuite(fiches, decls)
        for o in obs:
            assert not (o.nom == "contrapositive"
                        and o.kind == "vacuite_conclusion"), \
                f"faux positif : {o.apres}"

    def test_pas_de_prescriptif(self, corpus):
        fiches, decls = scanner_complet(corpus)
        obs = detecter_vacuite(fiches, decls)
        texte = formater_console(obs, [])
        bas = texte.lower()
        for p in LISTE_NOIRE_PRESCRIPTIF:
            assert p not in bas, f"langage prescriptif : {p}"


# ────────────────────────────────────────────────────────
# Hypothèses inutilisées
# ────────────────────────────────────────────────────────

CODE_INUTILISE = """\
theorem utilise_tout (x : Nat) (h : x > 0) : x > 0 := h

theorem ignore_h (x : Nat) (h : x > 0) : Nat := x
"""


class TestHypothesesInutilisees:
    @pytest.fixture
    def corpus(self, tmp_path):
        f = tmp_path / "cas.lean"
        f.write_text(CODE_INUTILISE)
        return str(tmp_path)

    def test_detecte_inutilisee(self, corpus):
        fiches, decls = scanner_complet(corpus)
        obs = detecter_hypotheses_inutilisees(fiches, decls)
        signales = [(o.nom, o.apres) for o in obs
                    if o.kind == "hypothese_inutilisee"]
        assert any(n == "ignore_h" and "«h»" in a for n, a in signales), \
            signales

    def test_ne_signale_pas_utilisee(self, corpus):
        fiches, decls = scanner_complet(corpus)
        obs = detecter_hypotheses_inutilisees(fiches, decls)
        for o in obs:
            if o.kind == "hypothese_inutilisee":
                assert "utilise_tout" not in o.nom or "«x»" not in o.apres


# ────────────────────────────────────────────────────────
# Inventaire axiomes
# ────────────────────────────────────────────────────────

CODE_AXIOMES = """\
axiom ax_un : True
axiom ax_deux : 1 = 1

theorem avec_axiome : True := trivial
"""


class TestInventaireAxiomes:
    @pytest.fixture
    def corpus(self, tmp_path):
        f = tmp_path / "cas.lean"
        f.write_text(CODE_AXIOMES)
        return str(tmp_path)

    def test_inventaire(self, corpus):
        fiches, _ = scanner_complet(corpus)
        inv = inventaire_axiomes(fiches)
        noms = {a.nom for a in inv}
        assert noms == {"ax_un", "ax_deux"}
        assert all(a.ligne > 0 for a in inv)


# ────────────────────────────────────────────────────────
# Câblage CLI : les exécuteurs doivent exister
# (régression 2026-10-08 : radar/sismique/consigner/registre
#  étaient câblés sans exécuteur → NameError)
# ────────────────────────────────────────────────────────

class TestCablageCLI:
    def test_executeurs_definis(self):
        import inspect
        from phi_complexity import cli
        src = inspect.getsource(cli)
        for nom in ["_executer_radar", "_executer_sismique",
                    "_executer_consigner", "_executer_registre",
                    "_executer_vigilance"]:
            assert f"def {nom}" in src, f"{nom} manquant dans cli.py"

    def test_vigilance_cli_fonctionne(self, tmp_path):
        import subprocess, sys, json, os
        f = tmp_path / "t.lean"
        f.write_text("def P (T : Nat) : Prop := T < T\n"
                     "theorem x (h : P 5) : True := trivial\n")
        racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(
            [sys.executable, "-m", "phi_complexity", "vigilance",
             str(tmp_path), "--format", "json", "--sans-inutilisees"],
            capture_output=True, text=True, cwd=racine,
            timeout=60)
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        kinds = [o["kind"] for o in data["observations"]]
        assert "vacuite_hypothese" in kinds, data
