"""
tests/test_durcissement_20261001.py — Durcissement « fail-loud » du 2026-10-01.

Couvre ce que test_durcissement.py (v0.6.1, indexeur) et
test_durcissement_sondes.py (2026-09-30, sondes) ne couvraient pas :

1. `phi veille` rend INSTRUMENT DÉGRADÉ + exit 3 quand l'arrière-plan
   d'analyse est perdu (incident réel du 2026-09-30 : fausse
   « dégradation » 3531 → 33 symboles après un reboot qui avait effacé
   tree-sitter-language-pack). Depuis l'autonomie stricte (2026-10-03),
   Lean est immunisé (parseur autonome) : le chemin dégradé est exercé
   via un langage encore dépendant de tree-sitter (JS dans le fixture).
2. `phi snapshot` REFUSE une baseline dégradée sans --force (exit 3),
   et l'accepte avec --force (exit 0, en connaissance de cause).
3. `phi chemins` surface l'avertissement d'arrière-plan (fix d'audit
   2026-10-01 : il restait silencieux là où `phi index` prévenait).
4. Le shim ~/workspace/bin/phi utilise le venv dédié quand présent,
   et replie sur python3 sinon (durcissement 2026-10-01).
"""
import argparse
import os
import subprocess
import textwrap

import pytest

from phi_complexity import cli
from phi_complexity.veille import prendre_snapshot
import phi_complexity.langs.registry as registre


@pytest.fixture
def projet_mixte(tmp_path):
    """Projet minimal : Python (natif), Lean (autonome depuis 2026-10-03)
    et JS (encore dépendant de tree-sitter — c'est lui qui exerce le
    chemin "arrière-plan perdu", le .lean en étant désormais immunisé)."""
    (tmp_path / "a.py").write_text(
        textwrap.dedent("""\
            def alpha(x):
                return x + 1
            """), encoding="utf-8")
    (tmp_path / "b.lean").write_text("def beta : Nat := 42\n",
                                     encoding="utf-8")
    (tmp_path / "c.js").write_text("function gamma() { return 1; }\n",
                                   encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def sans_arriere_plan(monkeypatch):
    """Simule l'incident du 2026-09-30 : tree-sitter effacé par un reboot."""
    monkeypatch.setattr(registre, "treesitter_disponible", lambda: False)
    return registre


def _ns(dossier, **kw):
    base = {"dossier": dossier, "format": "console", "lang": None,
            "exclude": None, "no_exclude": False, "out": None, "ref": None,
            "force": False, "top": 10, "exact": False}
    base.update(kw)
    return argparse.Namespace(**base)


class TestVeilleInstrumentDegrade:
    def test_exit3_et_bandeau_quand_arriere_plan_perdu(
            self, projet_mixte, monkeypatch, tmp_path, capsys):
        # Référence saine (arrière-plan présent), puis panne simulée :
        # la veille REFUSE le verdict au lieu d'une fausse dégradation.
        from phi_complexity.veille import ecrire_snapshot
        ref_path = str(tmp_path / "ref.json")
        ecrire_snapshot(projet_mixte, ref_path)  # arrière-plan sain
        monkeypatch.setattr(registre, "treesitter_disponible",
                            lambda: False)      # incident 2026-09-30
        code = cli._executer_veille(_ns(projet_mixte, ref=ref_path))
        sortie = capsys.readouterr()
        assert code == 3
        assert "INSTRUMENT DÉGRADÉ" in sortie.out
        assert "✅" not in sortie.out  # jamais de ✅ sur instrument en cause

    def test_controle_veille_saine_exit0(
            self, projet_mixte, tmp_path, capsys):
        """Contrôle : arrière-plan sain → STABLE, exit 0."""
        ref_path = str(tmp_path / "ref.json")
        from phi_complexity.veille import ecrire_snapshot
        ecrire_snapshot(projet_mixte, ref_path)
        code = cli._executer_veille(_ns(projet_mixte, ref=ref_path))
        sortie = capsys.readouterr().out
        assert code == 0
        assert "STABLE" in sortie

    def test_veille_json_porte_le_flag(
            self, projet_mixte, sans_arriere_plan, tmp_path, capsys):
        """En mode JSON, le verdict reste lisible par la machine."""
        import json
        ref_path = str(tmp_path / "ref.json")
        # Référence prise AVANT la panne (on triche honnêtement : on
        # restaure le pack le temps du snapshot).
        sans_arriere_plan.treesitter_disponible = lambda: True
        from phi_complexity.veille import ecrire_snapshot
        ecrire_snapshot(projet_mixte, ref_path)
        sans_arriere_plan.treesitter_disponible = lambda: False
        code = cli._executer_veille(_ns(projet_mixte, ref=ref_path,
                                        format="json"))
        doc = json.loads(capsys.readouterr().out)
        assert code == 3
        assert doc["verdict"] == "INSTRUMENT DÉGRADÉ"
        assert doc["instrument_degrade"] is True
        assert doc["causes_instrument"], "causes exigées, jamais vide"


class TestSnapshotRefuseBaselineDegradee:
    def test_refus_sans_force(self, projet_mixte, sans_arriere_plan,
                              tmp_path, capsys):
        """Baseline sur instrument dégradé : REFUSÉE, exit 3, rien écrit."""
        sortie_path = str(tmp_path / "snap.json")
        code = cli._executer_snapshot(_ns(projet_mixte, out=sortie_path))
        texte = capsys.readouterr().out
        assert code == 3
        assert "REFUSÉE" in texte
        assert not os.path.exists(sortie_path)

    def test_force_accepte_en_connaissance_de_cause(
            self, projet_mixte, sans_arriere_plan, tmp_path, capsys):
        """--force : l'utilisateur assume, la baseline est écrite."""
        sortie_path = str(tmp_path / "snap.json")
        code = cli._executer_snapshot(
            _ns(projet_mixte, out=sortie_path, force=True))
        assert code == 0
        assert os.path.exists(sortie_path)

    def test_snapshot_sain_exit0_controle(
            self, projet_mixte, tmp_path, capsys):
        """Contrôle : arrière-plan sain → snapshot écrit, exit 0."""
        sortie_path = str(tmp_path / "snap.json")
        code = cli._executer_snapshot(_ns(projet_mixte, out=sortie_path))
        assert code == 0
        assert os.path.exists(sortie_path)


class TestCheminsSignaleArrierePlan:
    def test_avertissement_quand_arriere_plan_perdu(
            self, projet_mixte, sans_arriere_plan, capsys):
        """Fix d'audit 2026-10-01 : `chemins` ne reste plus silencieux."""
        code = cli._executer_chemins(_ns(projet_mixte))
        sortie = capsys.readouterr().out
        assert code == 0  # commande informative : pas de changement de contrat
        assert "AVERTISSEMENT" in sortie
        assert "b.lean" in sortie

    def test_silencieux_quand_sain(self, projet_mixte, capsys):
        """Contrôle : arrière-plan sain → aucun avertissement."""
        code = cli._executer_chemins(_ns(projet_mixte))
        sortie = capsys.readouterr().out
        assert code == 0
        assert "AVERTISSEMENT" not in sortie

    def test_json_reste_valide_avertissement_sur_stderr(
            self, projet_mixte, sans_arriere_plan, capsys):
        """En mode JSON, l'avertissement ne corrompt pas le document."""
        import json
        code = cli._executer_chemins(_ns(projet_mixte, format="json"))
        capture = capsys.readouterr()
        assert code == 0
        doc = json.loads(capture.out)  # lève si corrompu
        assert isinstance(doc, dict)
        assert "AVERTISSEMENT" in capture.err


SHIM = os.path.expanduser("~/workspace/bin/phi")
V110 = os.path.expanduser("~/workspace/phi-complexity/v110")


@pytest.mark.skipif(not os.access(SHIM, os.X_OK),
                    reason="shim ~/workspace/bin/phi absent")
class TestShimVenv:
    def _fake_home(self, tmp_path, avec_venv):
        """HOME factice : phi-complexity symlinké, venv présent ou non."""
        home = tmp_path / "fakehome"
        (home / "workspace").mkdir(parents=True)
        os.symlink(V110, home / "workspace" / "phi-complexity")
        if avec_venv == "faux":
            bindir = home / "workspace" / "venvs" / "phi" / "bin"
            bindir.mkdir(parents=True)
            faux = bindir / "python"
            faux.write_text("#!/bin/sh\necho \"FAUX_VENV_UTILISE $@\"\n",
                            encoding="utf-8")
            os.chmod(faux, 0o755)
        return str(home)

    def test_repli_python3_sans_venv(self, tmp_path):
        """Sans venv : le shim replie sur python3 et démarre quand même."""
        env = dict(os.environ, HOME=self._fake_home(tmp_path, "non"))
        proc = subprocess.run([SHIM, "--version"], env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0
        assert "phi-complexity" in proc.stdout

    def test_venv_prioritaire_quand_present(self, tmp_path):
        """Avec venv : le shim l'utilise (pas le python3 système)."""
        env = dict(os.environ, HOME=self._fake_home(tmp_path, "faux"))
        proc = subprocess.run([SHIM, "--version"], env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0
        assert "FAUX_VENV_UTILISE" in proc.stdout
