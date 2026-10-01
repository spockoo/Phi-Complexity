"""
tests/test_cli.py — Tests de la CLI via subprocess et fonctions internes.
"""
import os
import sys
import json
import tempfile
import textwrap
import subprocess
from phi_complexity.cli import _collecter_fichiers, _nom_rapport


CODE_TEST = """
def ajouter(a: float, b: float) -> float:
    return a + b

def multiplier(a: float, b: float) -> float:
    return a * b
"""


def creer_fichier(code: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(code))
        return f.name


class TestCollecterFichiers:
    """Tests de la fonction de collecte de fichiers (sans subprocess)."""

    def test_fichier_python_direct(self):
        """Un fichier .py seul est retourné tel quel."""
        fichier = creer_fichier(CODE_TEST)
        try:
            resultat = _collecter_fichiers(fichier)
            assert resultat == [fichier]
        finally:
            os.unlink(fichier)

    def test_fichier_non_supporte(self):
        """Un fichier d'extension non supportée retourne une liste vide."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hello")
            chemin = f.name
        try:
            assert _collecter_fichiers(chemin) == []
        finally:
            os.unlink(chemin)

    def test_chemin_inexistant(self):
        """Un chemin inexistant retourne une liste vide."""
        assert _collecter_fichiers("/chemin/inexistant/fichier.py") == []

    def test_dossier_collecte_recursif(self):
        """Un dossier retourne tous les fichiers supportés récursivement."""
        dossier = tempfile.mkdtemp()
        f1 = os.path.join(dossier, "a.py")
        f2 = os.path.join(dossier, "b.txt")
        sous = os.path.join(dossier, "sub")
        os.makedirs(sous)
        f3 = os.path.join(sous, "c.py")
        for f in [f1, f3]:
            with open(f, "w") as fh:
                fh.write("x = 1")
        with open(f2, "w") as fh:
            fh.write("pas python")
        try:
            resultat = _collecter_fichiers(dossier)
            assert f1 in resultat
            assert f3 in resultat
            assert f2 not in resultat
        finally:
            import shutil
            shutil.rmtree(dossier)

    def test_dossier_collecte_multilangage(self):
        """Un dossier mixte doit collecter .py ET .js ensemble."""
        dossier = tempfile.mkdtemp()
        f_py = os.path.join(dossier, "a.py")
        f_js = os.path.join(dossier, "b.js")
        for f in [f_py, f_js]:
            with open(f, "w") as fh:
                fh.write("x = 1")
        try:
            resultat = _collecter_fichiers(dossier)
            assert f_py in resultat
            assert f_js in resultat
        finally:
            import shutil
            shutil.rmtree(dossier)


class TestNomRapport:

    def test_sortie_specifiee(self):
        assert _nom_rapport("mon_script.py", "mon_rapport.md") == "mon_rapport.md"

    def test_sortie_automatique(self):
        """Sans sortie spécifiée, génère RAPPORT_PHI_<nom>.md."""
        assert _nom_rapport("mon_script.py", None) == "RAPPORT_PHI_mon_script.md"

    def test_sortie_avec_chemin(self):
        """Le chemin absolu du fichier source est bien géré."""
        resultat = _nom_rapport("/home/user/src/mon_script.py", None)
        assert resultat == "RAPPORT_PHI_mon_script.md"


class TestCLISubprocess:
    """Tests de la CLI via subprocess — valide le point d'entrée réel."""

    def _phi(self, *args) -> subprocess.CompletedProcess:
        """Lance phi via 'python -m phi_complexity' (cross-platform fiable)."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-m", "phi_complexity"] + list(args),
            capture_output=True, text=True, encoding="utf-8", env=env
        )

    def test_version(self):
        """phi --version retourne la version."""
        res = self._phi("--version")
        assert "phi-complexity" in res.stdout or "phi-complexity" in res.stderr

    def test_check_fichier_simple(self):
        """phi check <fichier> retourne exit code 0 et affiche RADIANCE."""
        fichier = creer_fichier(CODE_TEST)
        try:
            res = self._phi("check", fichier)
            assert res.returncode == 0
            assert "RADIANCE" in res.stdout
        finally:
            os.unlink(fichier)

    def test_check_format_json(self):
        """phi check --format json retourne du JSON valide."""
        fichier = creer_fichier(CODE_TEST)
        try:
            res = self._phi("check", fichier, "--format", "json")
            assert res.returncode == 0
            data = json.loads(res.stdout)
            assert "radiance" in data
        finally:
            os.unlink(fichier)

    def test_check_min_radiance_passe(self):
        """Un fichier harmonieux avec seuil bas → exit 0."""
        fichier = creer_fichier(CODE_TEST)
        try:
            res = self._phi("check", fichier, "--min-radiance", "40")
            assert res.returncode == 0
        finally:
            os.unlink(fichier)

    def test_check_min_radiance_echoue(self):
        """Seuil impossible (101) → exit 1 car radiance <= 100 toujours."""
        fichier = creer_fichier(CODE_TEST)
        try:
            res = self._phi("check", fichier, "--min-radiance", "101")
            assert res.returncode == 1
        finally:
            os.unlink(fichier)

    def test_report_genere_fichier(self):
        """phi report <fichier> génère un .md."""
        fichier = creer_fichier(CODE_TEST)
        sortie = tempfile.mktemp(suffix=".md")
        try:
            res = self._phi("report", fichier, "--output", sortie)
            assert res.returncode == 0
            assert os.path.exists(sortie)
            with open(sortie, encoding="utf-8") as f:
                assert "RADIANCE" in f.read()
        finally:
            os.unlink(fichier)
            if os.path.exists(sortie):
                os.unlink(sortie)
