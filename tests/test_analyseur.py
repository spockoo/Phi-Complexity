"""
tests/test_analyseur.py — Tests d'intégration sur le moteur d'analyse AST (Python natif).
"""
import os
import textwrap
import tempfile
from phi_complexity.analyseur import AnalyseurPhi
from phi_complexity import auditer


def creer_fichier_temp(code: str) -> str:
    """Crée un fichier Python temporaire avec le code donné. Retourne le chemin."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(code))
        return f.name


class TestAnalyseurPhi:

    def test_charger_et_analyser(self):
        """L'analyseur peut charger et analyser un fichier simple."""
        code = """
        def bonjour():
            return "Hello, φ-Meta!"
        """
        fichier = creer_fichier_temp(code)
        try:
            analyseur = AnalyseurPhi(fichier)
            resultat = analyseur.analyser()
            assert len(resultat.fonctions) == 1
            assert resultat.fonctions[0].nom == "bonjour"
        finally:
            os.unlink(fichier)

    def test_detection_oudjat(self):
        """La fonction la plus complexe est bien identifiée comme Oudjat."""
        code = """
        def simple():
            return 1

        def complexe(a, b, c):
            for i in range(a):
                for j in range(b):
                    if i > j:
                        c += i * j
            return c
        """
        fichier = creer_fichier_temp(code)
        try:
            analyseur = AnalyseurPhi(fichier)
            resultat = analyseur.analyser()
            assert resultat.oudjat is not None
            assert resultat.oudjat.nom == "complexe"
        finally:
            os.unlink(fichier)

    def test_annotation_boucle_imbriquee(self):
        """Les boucles imbriquées doivent générer une annotation LILITH."""
        code = """
        def trop_imbrique():
            for i in range(10):
                for j in range(10):
                    pass
        """
        fichier = creer_fichier_temp(code)
        try:
            analyseur = AnalyseurPhi(fichier)
            resultat = analyseur.analyser()
            categories = [a.categorie for a in resultat.annotations]
            assert "LILITH" in categories
        finally:
            os.unlink(fichier)

    def test_annotation_open_sans_context(self):
        """Un open() sans 'with' doit générer une annotation SUTURE."""
        code = """
        def lire_fichier():
            f = open("data.txt", "r")
            contenu = f.read()
            f.close()
            return contenu
        """
        fichier = creer_fichier_temp(code)
        try:
            analyseur = AnalyseurPhi(fichier)
            resultat = analyseur.analyser()
            categories = [a.categorie for a in resultat.annotations]
            assert "SUTURE" in categories
        finally:
            os.unlink(fichier)

    def test_pas_d_annotation_open_avec_context(self):
        """Un open() avec 'with' ne doit PAS générer d'annotation SUTURE."""
        code = """
        def lire_proprement():
            with open("data.txt", "r") as f:
                return f.read()
        """
        fichier = creer_fichier_temp(code)
        try:
            analyseur = AnalyseurPhi(fichier)
            resultat = analyseur.analyser()
            categories = [a.categorie for a in resultat.annotations]
            assert "SUTURE" not in categories
        finally:
            os.unlink(fichier)

    def test_fichier_vide_sans_fonctions(self):
        """Un fichier sans fonctions retourne un résultat neutre."""
        code = """
        # Simple constante
        MA_CONSTANTE = 42
        """
        fichier = creer_fichier_temp(code)
        try:
            resultat_dict = auditer(fichier)
            assert resultat_dict["nb_fonctions"] == 0
            assert resultat_dict["radiance"] == 60.0  # Score neutre
        finally:
            os.unlink(fichier)


class TestCalculateurRadiance:

    def test_code_harmonieux_score_eleve(self):
        """Un code simple et propre doit avoir un score de radiance élevé."""
        code = """
        def calculer_phi(n: int) -> float:
            \"\"\"Retourne approximation de phi après n itérations Fibonacci.\"\"\"
            a, b = 1, 1
            for _ in range(n):
                a, b = b, a + b
            return b / a

        def normaliser(valeur: float, mini: float, maxi: float) -> float:
            \"\"\"Normalise une valeur entre 0 et 1.\"\"\"
            if maxi == mini:
                return 0.0
            return (valeur - mini) / (maxi - mini)
        """
        fichier = creer_fichier_temp(code)
        try:
            metriques = auditer(fichier)
            assert metriques["radiance"] >= 70, (
                f"Code harmonieux devrait scorer >= 70, obtenu: {metriques['radiance']}"
            )
        finally:
            os.unlink(fichier)

    def test_radiance_bornee_40_100(self):
        """Le score de radiance doit toujours être entre 40 et 100."""
        # Code très chaotique
        code = """
        def enfer(a,b,c,d,e,f,g):
            x=0
            for i in range(a):
                for j in range(b):
                    for k in range(c):
                        if i>j:
                            if j>k:
                                x+=i*j*k*d*e*f*g
            return x
        """
        fichier = creer_fichier_temp(code)
        try:
            metriques = auditer(fichier)
            assert 40 <= metriques["radiance"] <= 100
        finally:
            os.unlink(fichier)

    def test_phi_ratio_calcule(self):
        """Le phi_ratio doit être calculé et présent dans les métriques."""
        code = """
        def a(): return 1
        def b(x, y):
            for i in range(x):
                y += i
            return y
        """
        fichier = creer_fichier_temp(code)
        try:
            metriques = auditer(fichier)
            assert "phi_ratio" in metriques
            assert metriques["phi_ratio"] > 1.0  # La grande fonction domine
        finally:
            os.unlink(fichier)

    def test_zeta_score_present_et_valide(self):
        """Le zeta_score doit être entre 0 et 1."""
        code = """
        def f1(): pass
        def f2(): pass
        def f3(): pass
        """
        fichier = creer_fichier_temp(code)
        try:
            metriques = auditer(fichier)
            assert 0 <= metriques["zeta_score"] <= 1
        finally:
            os.unlink(fichier)
