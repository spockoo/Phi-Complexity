"""
metriques.py — Calcul de l'Indice de Radiance et des métriques φ-Meta.
Suturée selon les recommandations de phi-complexity v0.1.0 (Protocole BMAD).
'calculer()' décomposée en fonctions hermétiques — Règle I + Règle IV (Fibonacci).
Formule fondatrice : Radiance = 100 - f(Var_Lilith) - g(Entropie) - h(Anomalies) - i(Fibonacci)
Indépendante du langage source : opère uniquement sur ResultatAnalyse (générique).
"""
import math
from typing import List

from .core import (
    PHI, PHI_INV, TAXE_SUTURE, ETA_GOLDEN,
    SEUIL_LILITH_RELATIF, ENTROPIE_CIBLE_NORMALISEE, BANDE_TOLERANCE_ADIABATIQUE,
    statut_gnostique, calculer_antifragilite, statut_antifragile
)
from .modeles import ResultatAnalyse


class CalculateurRadiance:
    """
    Transforme les métriques brutes en Indice de Radiance (0-100).
    Ancrage : AX-A39 (Attracteur Doré) + EQ-AFR-BMAD (Loi Antifragile).
    Supporte le mode 'adiabatique' (universel, invariant d'échelle) et 'classique'.
    """

    def __init__(self, resultat: ResultatAnalyse, mode: str = "adiabatique"):
        self.r = resultat
        self.mode = mode

    # ────────────────────────────────────────────────────────
    # API PUBLIQUE
    # ────────────────────────────────────────────────────────

    def calculer(self) -> dict:
        """Orchestre le calcul — délègue tout aux fonctions spécialisées."""
        if not self.r.fonctions:
            return self._resultat_vide()
        brutes = self._extraire_mesures()
        rad_classique = self._indice_radiance(brutes)
        rad_adiabatique = self._indice_radiance_adiabatique(brutes)
        radiance = rad_adiabatique if self.mode == "adiabatique" else rad_classique
        return self._assembler_resultat(brutes, radiance, rad_adiabatique, rad_classique)

    # ────────────────────────────────────────────────────────
    # EXTRACTION DES MESURES BRUTES (hermétique)
    # ────────────────────────────────────────────────────────

    def _extraire_mesures(self) -> dict:
        """Extrait toutes les mesures brutes depuis le résultat d'analyse."""
        complexites = [f.complexite for f in self.r.fonctions]
        n_fonctions = len(self.r.fonctions)
        mean_c = sum(complexites) / n_fonctions if n_fonctions else 0.0
        var_c = self._variance(complexites)
        var_rel = (var_c / (mean_c ** 2)) if mean_c > 0 else 0.0

        h_brute = self._entropie_shannon(complexites)
        h_max = math.log2(n_fonctions) if n_fonctions > 1 else 1.0
        h_norm = (h_brute / h_max) if h_max > 0 else 0.0

        fib_total = sum(f.distance_fib for f in self.r.fonctions)
        fib_moy = (fib_total / n_fonctions) if n_fonctions > 0 else 0.0

        skewness, kurtosis = self._moments_superieurs(complexites)
        alpha_anti = calculer_antifragilite(var_rel)
        verdict_anti = statut_antifragile(alpha_anti)

        return {
            "complexites": complexites,
            "lilith_variance": var_c,
            "lilith_rel_variance": var_rel,
            "lilith_skewness": skewness,
            "lilith_kurtosis": kurtosis,
            "antifragilite": alpha_anti,
            "statut_antifragile": verdict_anti,
            "shannon_entropy": h_brute,
            "shannon_entropy_norm": h_norm,
            "phi_ratio": self._phi_ratio(complexites),
            "fibonacci_distance": fib_total,
            "fibonacci_distance_moyenne": fib_moy,
            "zeta_score": self._zeta_score(complexites),
            "nb_anomalies": len([
                a for a in self.r.annotations
                if a.niveau in ("WARNING", "CRITICAL")
            ]),
        }

    # ────────────────────────────────────────────────────────
    # ASSEMBLAGE DU RÉSULTAT (hermétique)
    # ────────────────────────────────────────────────────────

    def _assembler_resultat(self, brutes: dict, radiance: float,
                            rad_adiabatique: float, rad_classique: float) -> dict:
        """Construit le dictionnaire final à partir des mesures et du score."""
        phi_ratio = brutes["phi_ratio"]
        return {
            "fichier": self.r.fichier,
            "langage": self.r.langage,
            "radiance": round(radiance, 2),
            "radiance_adiabatique": round(rad_adiabatique, 2),
            "radiance_classique": round(rad_classique, 2),
            "mode_calcul": self.mode,
            "statut_gnostique": statut_gnostique(radiance),
            "statut_antifragile": brutes["statut_antifragile"],
            "antifragilite": round(brutes["antifragilite"], 3),
            "lilith_variance": round(brutes["lilith_variance"], 3),
            "lilith_rel_variance": round(brutes["lilith_rel_variance"], 4),
            "lilith_skewness": round(brutes["lilith_skewness"], 3),
            "lilith_kurtosis": round(brutes["lilith_kurtosis"], 3),
            "shannon_entropy": round(brutes["shannon_entropy"], 3),
            "shannon_entropy_norm": round(brutes["shannon_entropy_norm"], 3),
            "phi_ratio": round(phi_ratio, 3),
            "phi_ratio_delta": round(abs(phi_ratio - PHI), 3),
            "fibonacci_distance": round(brutes["fibonacci_distance"], 3),
            "fibonacci_distance_moyenne": round(brutes["fibonacci_distance_moyenne"], 3),
            "zeta_score": round(brutes["zeta_score"], 4),
            "nb_fonctions": len(self.r.fonctions),
            "nb_classes": self.r.nb_classes,
            "nb_imports": self.r.nb_imports,
            "nb_lignes_total": self.r.nb_lignes_total,
            "ratio_commentaires": round(
                self.r.nb_commentaires / max(1, self.r.nb_lignes_total), 3
            ),
            "oudjat": self._serialiser_oudjat(),
            "annotations": self._serialiser_annotations(),
        }

    def _serialiser_oudjat(self) -> dict:
        """Sérialise la fonction Oudjat (la plus complexe) en dictionnaire."""
        if not self.r.oudjat:
            return None
        o = self.r.oudjat
        return {
            "nom": o.nom,
            "ligne": o.ligne,
            "complexite": o.complexite,
            "nb_lignes": o.nb_lignes,
            "phi_ratio": round(o.phi_ratio, 3),
        }

    def _serialiser_annotations(self) -> list:
        """Sérialise la liste des annotations en dictionnaires."""
        return [
            {
                "ligne": a.ligne,
                "niveau": a.niveau,
                "categorie": a.categorie,
                "message": a.message,
                "extrait": a.extrait,
            }
            for a in self.r.annotations
        ]

    # ────────────────────────────────────────────────────────
    # FORMULE ADIABATIQUE ET UNIVERSELLE (v0.3.0)
    # ────────────────────────────────────────────────────────

    def _indice_radiance_adiabatique(self, brutes: dict) -> float:
        """
        R_adia = 100 - f_adia(Var_rel) - g_adia(H_norm) - h(Anomalies) - i_adia(D_moy)
        Invariance d'échelle universelle et équilibre réversible par décomposition modulaire.
        Plancher : 40.0.
        """
        score = 100.0
        score -= self._deduction_lilith_adiabatique(brutes["lilith_rel_variance"])
        score -= self._deduction_entropie_adiabatique(brutes["shannon_entropy_norm"])
        score -= self._deduction_anomalies(brutes["nb_anomalies"])
        score -= self._deduction_fibonacci_adiabatique(brutes["fibonacci_distance_moyenne"])
        return max(40.0, score)

    def _deduction_lilith_adiabatique(self, var_rel: float) -> float:
        """
        f_adia(Var_rel) = min(25, max(0, (Var_rel - φ⁻¹) / φ) × 25).
        Invariant d'échelle : Var_rel = σ² / μ². Seuil naturel doré : φ⁻¹ ≈ 0.618.
        """
        if var_rel <= SEUIL_LILITH_RELATIF:
            return 0.0
        return min(25.0, ((var_rel - SEUIL_LILITH_RELATIF) / PHI) * 25.0)

    def _deduction_entropie_adiabatique(self, h_norm: float) -> float:
        """
        g_adia(H_norm) : attracteur doré H_norm ≈ φ⁻¹.
        Pénalise la singularité (God-object monolithique où H_norm < 0.236)
        ou l'indifférenciation plate absolue (H_norm > 0.98 sur de grands ensembles).
        """
        seuil_bas = ENTROPIE_CIBLE_NORMALISEE - BANDE_TOLERANCE_ADIABATIQUE  # ≈ 0.236
        if h_norm < seuil_bas:
            return min(20.0, (seuil_bas - h_norm) * 40.0)
        elif h_norm > 0.98 and len(self.r.fonctions) >= 5:
            return min(5.0, (h_norm - 0.98) * 50.0)
        return 0.0

    def _deduction_fibonacci_adiabatique(self, dist_moy: float) -> float:
        """i_adia(D_moy) = min(10, D_moy × 2.0). Grandeur intensive."""
        return min(10.0, dist_moy * 2.0)

    # ────────────────────────────────────────────────────────
    # FORMULE FONDATRICE CLASSIQUE — INDICE DE RADIANCE (v0.2.0)
    # ────────────────────────────────────────────────────────

    def _indice_radiance(self, brutes: dict) -> float:
        """
        R = 100 - f(Lilith) - g(Shannon) - h(Anomalies) - i(Fibonacci)
        Chaque déduction est plafonnée (Loi d'Indulgence).
        Plancher : 40 (Loi Antifragile — EQ-AFR-BMAD).
        """
        score = 100.0
        score -= self._deduction_lilith(brutes["lilith_variance"])
        score -= self._deduction_entropie(brutes["shannon_entropy"])
        score -= self._deduction_anomalies(brutes["nb_anomalies"])
        score -= self._deduction_fibonacci(brutes["fibonacci_distance"])
        return max(40.0, score)

    def _deduction_lilith(self, variance: float) -> float:
        """f(Lilith) = min(25, (σ²_L / seuil) × 25). Seuil naturel = φ² × 100."""
        seuil = PHI ** 2 * 100
        return min(25.0, (variance / seuil) * 25.0)

    def _deduction_entropie(self, entropie: float) -> float:
        """g(H) = min(20, max(0, H - H_max) × 5). H_max = log₂(φ⁴) ≈ 2.88 bits."""
        seuil = math.log2(PHI ** 4)
        return min(20.0, max(0.0, entropie - seuil) * 5.0)

    def _deduction_anomalies(self, nb: int) -> float:
        """h(A) = min(30, A × τ_L × 3). τ_L = Taxe de Suture (CM-018)."""
        return min(30.0, nb * TAXE_SUTURE * 3)

    def _deduction_fibonacci(self, distance: float) -> float:
        """i(D_F) = min(10, D_F × η_golden)."""
        return min(10.0, distance * ETA_GOLDEN)

    # ────────────────────────────────────────────────────────
    # FORMULES MATHÉMATIQUES SOUVERAINES (atomiques)
    # ────────────────────────────────────────────────────────

    def _variance(self, valeurs: List[float]) -> float:
        """σ²_L = (1/n) · Σ(κᵢ - μ)². Variance de Lilith."""
        if not valeurs:
            return 0.0
        mean = sum(valeurs) / len(valeurs)
        return sum((v - mean) ** 2 for v in valeurs) / len(valeurs)

    def _entropie_shannon(self, valeurs: List[float]) -> float:
        """H = -Σ pᵢ · log₂(pᵢ). Entropie de Shannon normalisée."""
        if not valeurs:
            return 0.0
        total = sum(valeurs)
        if total == 0:
            return 0.0
        probas = [v / total for v in valeurs]
        return -sum(p * math.log2(p) for p in probas if p > 0)

    def _phi_ratio(self, valeurs: List[float]) -> float:
        """φ-ratio = max(κ) / μ. Doit tendre vers φ = 1.618."""
        if not valeurs or len(valeurs) < 2:
            return 1.0
        mean = sum(valeurs) / len(valeurs)
        return (max(valeurs) / mean) if mean else 1.0

    def _zeta_score(self, valeurs: List[float]) -> float:
        """ζ_meta = min(1, [Σ 1/(i+1)^φ / n] × φ). Résonance globale."""
        if not valeurs:
            return 0.0
        n = len(valeurs)
        zeta = sum(1.0 / ((i + 1) ** PHI) for i in range(n)) / n
        return min(1.0, zeta * PHI)

    def _moments_superieurs(self, valeurs: List[float]) -> tuple:
        """
        Moments d'ordre supérieur du spectre de Lilith :
        - Asymétrie (Skewness, γ₁) : orientation de la hiérarchie.
        - Aplatissement (Kurtosis, β₂) : présence de singularités / queues lourdes.
        """
        n = len(valeurs)
        if n < 3:
            return 0.0, 3.0
        mean = sum(valeurs) / n
        var = sum((v - mean) ** 2 for v in valeurs) / n
        std = math.sqrt(var)
        if std == 0:
            return 0.0, 3.0
        skew = sum(((v - mean) / std) ** 3 for v in valeurs) / n
        kurt = sum(((v - mean) / std) ** 4 for v in valeurs) / n
        return skew, kurt

    # ────────────────────────────────────────────────────────
    # RÉSULTAT NEUTRE (fichiers sans fonctions)
    # ────────────────────────────────────────────────────────

    def _resultat_vide(self) -> dict:
        """Score neutre (60) pour les fichiers de constantes ou de configuration."""
        return {
            "fichier": self.r.fichier,
            "langage": self.r.langage,
            "radiance": 60.0,
            "radiance_adiabatique": 60.0,
            "radiance_classique": 60.0,
            "mode_calcul": self.mode,
            "statut_gnostique": statut_gnostique(60.0),
            "statut_antifragile": "RÉSISTANT ◈",
            "antifragilite": round(PHI_INV, 3),
            "lilith_variance": 0.0,
            "lilith_rel_variance": 0.0,
            "lilith_skewness": 0.0,
            "lilith_kurtosis": 3.0,
            "shannon_entropy": 0.0,
            "shannon_entropy_norm": 0.0,
            "phi_ratio": 1.0,
            "phi_ratio_delta": PHI - 1.0,
            "fibonacci_distance": 0.0,
            "fibonacci_distance_moyenne": 0.0,
            "zeta_score": 0.0,
            "nb_fonctions": 0,
            "nb_classes": self.r.nb_classes,
            "nb_imports": self.r.nb_imports,
            "nb_lignes_total": self.r.nb_lignes_total,
            "ratio_commentaires": 0.0,
            "oudjat": None,
            "annotations": [],
        }
