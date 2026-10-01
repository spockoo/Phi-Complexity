"""
bayes.py — Moteur d'Inférence Bayésienne et d'Automatisation Chirurgicale pour phi-complexity.

Permet à phi-complexity de passer du diagnostic passif à la décision et l'action active :
1. Distribution Prior P(H) sur les hypothèses de défaillance / dette technique.
2. Vraisemblance P(E | H) conditionnée par le spectre morphique observé
   (Variance de Lilith, Asymétrie Skewness, Kurtosis, Shannon normalisée, Oudjat, Annotations).
3. Posterior P(H | E) par la formule de Bayes :
       P(H_k | E) = P(E | H_k) · P(H_k) / Σ_j P(E | H_j) · P(H_j)
4. Estimation Bayésienne de gain d'espérance de Radiance E[ΔR | Action]
   et sélection de la politique de refactorisation optimale.
5. Génération automatique de patchs et propositions de découpage chirurgical (auto-suture).
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import eft
from .core import PHI, PHI_INV, ETA_GOLDEN
from .eft import CroyanceCertifiee
from .oracle import TraceurOracle


# ────────────────────────────────────────────────────────
# HYPOTHÈSES BAYÉSIENNES DU SPECTRE MORPHIQUE
# ────────────────────────────────────────────────────────

HYPOTHESES_MORPHIC = [
    "H_MONOLITHE",       # Hypertrophie d'un organe unique (Oudjat destructeur)
    "H_CHAOS_IMBRIQUE",   # Boucles et cascades conditionnelles profondes (Lilith critique)
    "H_LEAK_RESSOURCE",   # Fuite de cycle de vie de descripteurs (RAII manquant)
    "H_COUPLED_ARITE",    # Fonctions surchargées en paramètres (Arite > 5)
    "H_HARMONIQUE",       # Code équilibré en résonance dorée
]

# Priors a priori par défaut (inspirés du Morphic Phi Framework)
PRIORS_PAR_DEFAUT: Dict[str, float] = {
    "H_MONOLITHE": 0.20,
    "H_CHAOS_IMBRIQUE": 0.25,
    "H_LEAK_RESSOURCE": 0.15,
    "H_COUPLED_ARITE": 0.15,
    "H_HARMONIQUE": 0.25,
}


@dataclass
class DiagnosticBayesien:
    """Résultat complet d'une inférence bayésienne sur un module."""
    posteriors: Dict[str, float]
    hypothese_dominante: str
    probabilite_dominante: float
    gain_espere_radiance: float
    action_recommandee: str
    auto_patch_suggestion: Optional[str] = None
    # --- EFT opt-in : inertes en mode flottant (défaut) ---
    mode: str = "flottant"
    certifie: Dict[str, CroyanceCertifiee] = field(default_factory=dict)


class MoteurInferenceBayesienne:
    """
    Moteur de décision bayésienne guidé par les invariants du nombre d'or.
    """

    def __init__(self, metriques: dict, priors: Optional[Dict[str, float]] = None,
                 exact: Optional[bool] = None,
                 traceur: Optional[TraceurOracle] = None,
                 cible: str = ""):
        self.m = metriques
        self.priors = priors or dict(PRIORS_PAR_DEFAUT)
        # exact=None -> drapeau d'environnement PHI_EFT (opt-in global) ;
        # exact=False explicite -> flottant même si PHI_EFT=1.
        self._exact_param = exact
        self.traceur = traceur
        self.cible = cible or "<metriques>"

    def _mode_exact(self) -> bool:
        """Le chemin EFT est-il demandé ? Jamais par défaut."""
        if self._exact_param is not None:
            return self._exact_param
        return eft.eft_active()

    def inferer(self) -> DiagnosticBayesien:
        """Calcule les probabilités a posteriori et sélectionne l'action optimale."""
        vraisemblances = self._calculer_vraisemblances()
        # NOTE HONNéTE : les vraisemblances sont des capteurs amont déjé
        # arrondis (sigmoédes, ratios) ; l'EFT ne couvre que l'arithmétique
        # du postérieur ci-dessous, é entrées fixées (voir eft.py).
        mode_exact = self._mode_exact()
        certifie: Dict[str, CroyanceCertifiee] = {}
        if mode_exact:
            certifie = self._calculer_posteriors_eft(vraisemblances)
            posteriors = {h: c.valeur for h, c in certifie.items()}
        else:
            posteriors = self._calculer_posteriors(vraisemblances)

        hyp_dom = max(posteriors.keys(), key=lambda k: posteriors[k])
        prob_dom = posteriors[hyp_dom]

        gain_espere, action, suggestion = self._evaluer_politique(hyp_dom, prob_dom)
        self._tracer(vraisemblances, posteriors, certifie, hyp_dom, action,
                     mode="eft" if mode_exact else "flottant")

        return DiagnosticBayesien(
            posteriors={k: round(v, 4) for k, v in posteriors.items()},
            hypothese_dominante=hyp_dom,
            probabilite_dominante=round(prob_dom, 4),
            gain_espere_radiance=round(gain_espere, 2),
            action_recommandee=action,
            auto_patch_suggestion=suggestion,
            mode="eft" if mode_exact else "flottant",
            certifie=certifie,
        )

    def _calculer_posteriors_eft(
            self, vraisemblances: Dict[str, float]) -> Dict[str, CroyanceCertifiee]:
        """Postérieur P(H|E) en arithmétique double-double certifiée.

        Méme formule que _calculer_posteriors :
            P(H_k|E) = L_k x prior_k / somme_j (L_j x prior_j)
        mais chaque produit est un two_prod exact, la norme une somme
        double-double, chaque quotient une division double-double.
        La borne de chaque croyance couvre cette arithmétique é
        entrées fixées (pas l'erreur des vraisemblances amont).
        """
        cert: Dict[str, CroyanceCertifiee] = {}
        for h in HYPOTHESES_MORPHIC:
            acc = eft.AccumulateurCertifie()
            acc.charger(vraisemblances[h])
            acc.multiplier(self.priors.get(h, 0.2))
            num_hi, num_lo = acc.valeur()
            # Norme : recalculée ici pour un compteur d'opérations
            # honnéte par postérieur (5 hypothéses : coét négligeable).
            acc_n = eft.AccumulateurCertifie()
            for h2 in HYPOTHESES_MORPHIC:
                p_hi, p_lo = eft.dd_mul(vraisemblances[h2], 0.0,
                                        self.priors.get(h2, 0.2), 0.0)
                acc_n.ajouter_dd(p_hi, p_lo)
                acc_n._bump(1)  # le two_prod du produit ci-dessus
            den_hi, den_lo = acc_n.valeur()
            if den_hi == 0.0 and den_lo == 0.0:
                # Repli identique au chemin flottant : uniforme.
                uni = 1.0 / len(HYPOTHESES_MORPHIC)
                cert[h] = CroyanceCertifiee(
                    nom=h, hi=uni, lo=0.0, borne_erreur=0.0,
                    termes={"vraisemblance": vraisemblances[h],
                            "prior": self.priors.get(h, 0.2)},
                    nb_operations=0, magnitude_max=0.0)
                continue
            q_hi, q_lo = eft.dd_div(num_hi, num_lo, den_hi, den_lo)
            n_ops = acc.n_ops + acc_n.n_ops + 3  # +3 : la division longue
            m_max = max(acc.magnitude_max, acc_n.magnitude_max, abs(q_hi))
            borne = eft.K_BORNE * n_ops * eft.U * eft.U * m_max
            cert[h] = CroyanceCertifiee(
                nom=h, hi=q_hi, lo=q_lo, borne_erreur=borne,
                termes={"vraisemblance": vraisemblances[h],
                        "prior": self.priors.get(h, 0.2),
                        "produit": num_hi + num_lo,
                        "norme": den_hi + den_lo},
                nb_operations=n_ops, magnitude_max=m_max)
        return cert

    def _tracer(self, vraisemblances: Dict[str, float],
                posteriors: Dict[str, float],
                certifie: Dict[str, CroyanceCertifiee],
                hyp_dom: str, action: str, mode: str) -> None:
        """Enregistre une trace d'oracle par hypothése (si traceur branché).

        La trace existe dans les deux modes : la chaéne
        prior -> évidences -> postérieur -> action ne dépend pas de
        l'EFT ; seul le mode eft ajoute la borne certifiée.
        """
        if self.traceur is None:
            return
        for h in HYPOTHESES_MORPHIC:
            c = certifie.get(h)
            self.traceur.enregistrer(
                moteur="bayes", cible=self.cible, symbole=h,
                prior=self.priors.get(h, 0.2),
                termes_evidence={"vraisemblance": vraisemblances[h],
                                   "prior": self.priors.get(h, 0.2)},
                posterior=posteriors[h],
                posterior_hi=(c.hi if c else None),
                posterior_lo=(c.lo if c else None),
                borne_erreur=(c.borne_erreur if c else None),
                decision=(action if h == hyp_dom else "non dominante"),
                mode=mode,
            )

    # ────────────────────────────────────────────────────────
    # VRAISEMBLANCES P(Evidence | Hypothèse)
    # ────────────────────────────────────────────────────────

    def _calculer_vraisemblances(self) -> Dict[str, float]:
        """Évalue P(E | H) selon le spectre morphique et les annotations."""
        m = self.m
        rel_var = m.get("lilith_rel_variance", 0.0)
        kurt = m.get("lilith_kurtosis", 3.0)
        phi_r = m.get("phi_ratio", 1.0)
        annotations = m.get("annotations", [])

        nb_lilith = len([a for a in annotations if a.get("categorie") == "LILITH"])
        nb_suture = len([a for a in annotations if a.get("categorie") == "SUTURE"])
        nb_arite = len([a for a in annotations if a.get("categorie") == "SOUVERAINETE"])

        # P(E | H_MONOLITHE) : favorisé par phi-ratio élevé et kurtosis lourd
        p_monolithe = self._sigmoide(phi_r - PHI, pente=1.2) * self._sigmoide(kurt - 4.5, pente=0.8)

        # P(E | H_CHAOS_IMBRIQUE) : favorisé par annotations LILITH et variance relative
        p_chaos = self._sigmoide(nb_lilith * 2.0 + rel_var - PHI_INV, pente=1.5)

        # P(E | H_LEAK_RESSOURCE) : lié directement aux violations RAII
        p_leak = 0.95 if nb_suture > 0 else 0.05

        # P(E | H_COUPLED_ARITE) : lié directement aux violations d'arité
        p_arite = 0.90 if nb_arite > 0 else 0.08

        # P(E | H_HARMONIQUE) : favorisé quand la radiance est haute et l'antifragilité forte
        alpha = m.get("antifragilite", PHI_INV)
        rad = m.get("radiance", 60.0)
        p_harmonique = (rad / 100.0) * alpha * (1.0 if not annotations else 0.2)

        # Garantir un plancher epsilon pour éviter toute probabilité nulle
        eps = 1e-4
        return {
            "H_MONOLITHE": max(eps, p_monolithe),
            "H_CHAOS_IMBRIQUE": max(eps, p_chaos),
            "H_LEAK_RESSOURCE": max(eps, p_leak),
            "H_COUPLED_ARITE": max(eps, p_arite),
            "H_HARMONIQUE": max(eps, p_harmonique),
        }

    def _calculer_posteriors(self, vraisemblances: Dict[str, float]) -> Dict[str, float]:
        """Applique la formule de Bayes : P(H_k | E) ∝ P(E | H_k) · P(H_k)."""
        produits = {h: vraisemblances[h] * self.priors.get(h, 0.2) for h in HYPOTHESES_MORPHIC}
        norme = sum(produits.values())
        if norme == 0:
            return {h: 1.0 / len(HYPOTHESES_MORPHIC) for h in HYPOTHESES_MORPHIC}
        return {h: val / norme for h, val in produits.items()}

    # ────────────────────────────────────────────────────────
    # POLITIQUE DE DÉCISION ET GAIN D'ESPÉRANCE E[ΔR | Action]
    # ────────────────────────────────────────────────────────

    def _evaluer_politique(self, hyp: str, prob: float) -> Tuple[float, str, Optional[str]]:
        """Détermine l'action chirurgicale prioritaire et le gain potentiel de Radiance."""
        m = self.m
        rad_actuelle = m.get("radiance", 70.0)
        gain_max_possible = max(0.0, 100.0 - rad_actuelle)

        if hyp == "H_MONOLITHE":
            o = m.get("oudjat")
            nom_o = o.get("nom", "organe") if o else "organe"
            gain_estime = min(gain_max_possible, 15.0 * prob + (o.get("phi_ratio", 2.0) - PHI) * 4.0)
            action = f"AUTO-SUTURE MONOLITHE : Partitionner '{nom_o}' en 2 sous-fonctions de taille Fibonacci."
            suggestion = (
                f"-- Suture bayésienne de '{nom_o}':\n"
                f"-- 1. Extraire la boucle / logique interne dans '{nom_o}_aux'\n"
                f"-- 2. Réduire la signature et rééquilibrer le phi-ratio vers {PHI:.3f}"
            )
            return gain_estime, action, suggestion

        elif hyp == "H_CHAOS_IMBRIQUE":
            gain_estime = min(gain_max_possible, 20.0 * prob)
            action = "APLATISSEMENT DES BOUCLES : Extraire les itérations imbriquées (LILITH) en combinateurs ou fonctions pures."
            suggestion = (
                "-- Suture bayésienne Lilith:\n"
                "-- Transformer les boucles 'for i in ...: for j in ...' en compréhensions ou fonctions auxiliaires d'étapes."
            )
            return gain_estime, action, suggestion

        elif hyp == "H_LEAK_RESSOURCE":
            gain_estime = min(gain_max_possible, 12.0 * prob)
            action = "ENCAPSULATION RAII : Envelopper les descripteurs ou handles dans des gestionnaires de contexte sécurisés."
            suggestion = (
                "-- Suture bayésienne RAII:\n"
                "-- Remplacer les ouvertures manuelles par 'with open(...) as f:' ou des blocs certifiés de gestion de ressources."
            )
            return gain_estime, action, suggestion

        elif hyp == "H_COUPLED_ARITE":
            gain_estime = min(gain_max_possible, 8.0 * prob)
            action = "ENCAPSULATION ENREGISTREMENT : Regrouper les arguments excédentaires (> 5) dans un DTO / Structure."
            suggestion = (
                "-- Suture bayésienne Souveraineté:\n"
                "-- Définir un type de configuration ou structure de paramètres pour encapsuler l'arité."
            )
            return gain_estime, action, suggestion

        else:  # H_HARMONIQUE
            gain_estime = min(gain_max_possible, 2.0)
            action = "MAINTIEN HOMÉOSTATIQUE : Le module est en équilibre morphique optimal (Hermétique)."
            return gain_estime, action, None

    @staticmethod
    def _sigmoide(x: float, pente: float = 1.0) -> float:
        """Fonction logistique standard pour probabilité continue."""
        try:
            return 1.0 / (1.0 + math.exp(-pente * x))
        except OverflowError:
            return 0.0 if x < 0 else 1.0
