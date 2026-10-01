"""
tests/test_rapport.py — Tests du générateur de rapports Console/Markdown/JSON.
"""
import json
import os
import textwrap
import tempfile
from phi_complexity import auditer, rapport_console, rapport_markdown, rapport_json
from phi_complexity.rapport import GenerateurRapport


def creer_fichier(code: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(code))
        return f.name


CODE_SIMPLE = """
def bonjour(nom: str) -> str:
    return f"Bonjour, {nom} !"

def au_revoir(nom: str) -> str:
    return f"Au revoir, {nom}."
"""

CODE_AVEC_ANNOTATION = """
def boucle_chaotique():
    for i in range(10):
        for j in range(10):
            pass
"""


class TestGenerateurConsole:

    def test_console_contient_radiance(self):
        fichier = creer_fichier(CODE_SIMPLE)
        try:
            sortie = rapport_console(fichier)
            assert "RADIANCE" in sortie
            assert "HERMÉTIQUE" in sortie or "EN ÉVEIL" in sortie or "DORMANT" in sortie
        finally:
            os.unlink(fichier)

    def test_console_contient_statut_gnostique(self):
        fichier = creer_fichier(CODE_SIMPLE)
        try:
            sortie = rapport_console(fichier)
            assert "STATUT" in sortie
        finally:
            os.unlink(fichier)

    def test_console_contient_phi_meta(self):
        """Le pied de page doit mentionner le Morphic Phi Framework."""
        fichier = creer_fichier(CODE_SIMPLE)
        try:
            sortie = rapport_console(fichier)
            assert "φ-Meta" in sortie
        finally:
            os.unlink(fichier)

    def test_console_signale_les_annotations(self):
        """Un code avec boucles imbriquées doit afficher les sutures."""
        fichier = creer_fichier(CODE_AVEC_ANNOTATION)
        try:
            sortie = rapport_console(fichier)
            assert "SUTURES" in sortie or "LILITH" in sortie
        finally:
            os.unlink(fichier)

    def test_barre_radiance_ascii(self):
        """La barre ASCII doit contenir des blocs █."""
        gen = GenerateurRapport({"radiance": 75.0, "fichier": "test.py",
                                  "statut_gnostique": "EN ÉVEIL ◈",
                                  "lilith_variance": 100.0, "shannon_entropy": 2.0,
                                  "phi_ratio": 1.7, "phi_ratio_delta": 0.08,
                                  "zeta_score": 0.5, "oudjat": None,
                                  "annotations": []})
        barre = gen._barre(75.0)
        assert "█" in barre
        assert "░" in barre


class TestGenerateurMarkdown:

    def test_markdown_contient_sections(self):
        fichier = creer_fichier(CODE_SIMPLE)
        try:
            md = rapport_markdown(fichier)
            assert "## 1. INDICE DE RADIANCE" in md
            assert "## 2. MÉTRIQUES SOUVERAINES" in md
            assert "## 4. REVUE DE DÉTAIL" in md
        finally:
            os.unlink(fichier)

    def test_markdown_sauvegarde_fichier(self):
        fichier = creer_fichier(CODE_SIMPLE)
        sortie = tempfile.mktemp(suffix=".md")
        try:
            rapport_markdown(fichier, sortie=sortie)
            assert os.path.exists(sortie)
            with open(sortie, encoding="utf-8") as f:
                contenu = f.read()
            assert "RADIANCE" in contenu
        finally:
            os.unlink(fichier)
            if os.path.exists(sortie):
                os.unlink(sortie)

    def test_markdown_mentions_phi_meta(self):
        fichier = creer_fichier(CODE_SIMPLE)
        try:
            md = rapport_markdown(fichier)
            assert "φ-Meta" in md or "Morphic Phi" in md
        finally:
            os.unlink(fichier)


class TestGenerateurJSON:

    def test_json_valide(self):
        """La sortie JSON doit être parseable."""
        fichier = creer_fichier(CODE_SIMPLE)
        try:
            sortie = rapport_json(fichier)
            data = json.loads(sortie)
            assert "radiance" in data
            assert "statut_gnostique" in data
            assert isinstance(data["radiance"], float)
        finally:
            os.unlink(fichier)

    def test_json_contient_toutes_metriques(self):
        fichier = creer_fichier(CODE_SIMPLE)
        try:
            data = json.loads(rapport_json(fichier))
            champs = [
                "radiance", "lilith_variance", "shannon_entropy",
                "phi_ratio", "zeta_score", "fibonacci_distance",
                "nb_fonctions", "annotations"
            ]
            for champ in champs:
                assert champ in data, f"Champ manquant : {champ}"
        finally:
            os.unlink(fichier)


class TestGenerateurSARIF:

    def test_sarif_schema_valide(self):
        """La sortie SARIF doit respecter le standard OASIS v2.1.0."""
        from phi_complexity import rapport_sarif
        fichier = creer_fichier(CODE_AVEC_ANNOTATION)
        try:
            sortie = rapport_sarif(fichier)
            data = json.loads(sortie)
            assert data["version"] == "2.1.0"
            assert "$schema" in data
            assert len(data["runs"]) == 1
            driver = data["runs"][0]["tool"]["driver"]
            assert driver["name"] == "phi-complexity"
            assert len(data["runs"][0]["results"]) >= 1
            assert data["runs"][0]["results"][0]["ruleId"] == "PHI-LILITH"
        finally:
            os.unlink(fichier)

    def test_prescriptions_generees(self):
        """Vérifie la génération des prescriptions d'équilibre."""
        from phi_complexity import auditer
        from phi_complexity.rapport import GenerateurRapport
        fichier = creer_fichier(CODE_AVEC_ANNOTATION)
        try:
            metriques = auditer(fichier)
            gen = GenerateurRapport(metriques)
            conseils = gen.prescriptions()
            assert any("Boucles imbriquées" in c for c in conseils)
        finally:
            os.unlink(fichier)
