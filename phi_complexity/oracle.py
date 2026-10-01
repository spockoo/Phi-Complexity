"""
oracle.py — Traces d'oracle pour phi-complexity (v0.11.0, opt-in EFT).

Un oracle n'est pas un devin : c'est une chaîne de raisonnement EXHIBÉE.
Chaque mise à jour de croyance enregistre :
    horodatage, moteur, cible, symbole/hypothèse, prior,
    termes d'évidence, postérieur, borne d'erreur certifiée,
    décision/action soutenue, mode arithmétique, limites.

En mode flottant (défaut), la trace existe aussi — la chaîne
prior → évidences → postérieur → action ne dépend pas de l'EFT ;
seule la borne d'erreur est alors `None` (« non certifiée »), et c'est
écrit en toutes lettres. L'EFT (PHI_EFT=1 ou --exact) ajoute la
certification arithmétique, pas la chaîne.

LIMITES (mêmes que croyances.py, rappelées à chaque sortie) :
- Les traces exhibent le RAISONNEMENT de l'instrument, pas une vérité.
  Un postérieur certifié à 2⁻¹⁰⁰ reste une heuristique d'inspiration
  bayésienne : l'exactitude arithmétique ne rend pas le modèle vrai.
- La borne certifiée ne couvre que l'arithmétique câblée en EFT, à
  entrées fixées (voir eft.py : PORTÉE DE LA BORNE).
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .core import VERSION


LIMITE_ORACLE = (
    "TRACE D'ORACLE — chaîne de raisonnement exhibée, pas une vérité. "
    "Le postérieur reste une HEURISTIQUE d'inspiration bayésienne "
    "(voir croyances.py : LIMITE_HONNETETE), même certifié à 2⁻¹⁰⁰ près : "
    "l'exactitude arithmétique ne rend pas le modèle vrai. La borne "
    "certifiée ne couvre que l'arithmétique EFT à entrées fixées "
    "(erreur des capteurs amont non couverte)."
)


@dataclass
class TraceEntree:
    """Une mise à jour de croyance, avec tout son contexte."""
    horodatage: str
    moteur: str            # "bayes" | "croyances"
    cible: str             # fichier ou dossier analysé
    symbole: str           # hypothèse (bayes) ou nom de symbole (croyances)
    prior: float
    termes_evidence: dict = field(default_factory=dict)
    posterior_hi: Optional[float] = None
    posterior_lo: Optional[float] = None
    posterior: Optional[float] = None   # meilleur flottant (hi+lo)
    borne_erreur: Optional[float] = None  # None = non certifiée (mode flottant)
    decision: str = ""
    mode: str = "flottant"  # "flottant" | "eft"

    def vers_dict(self) -> dict:
        return {
            "horodatage": self.horodatage,
            "moteur": self.moteur,
            "cible": self.cible,
            "symbole": self.symbole,
            "prior": self.prior,
            "termes_evidence": dict(self.termes_evidence),
            "posterior_hi": self.posterior_hi,
            "posterior_lo": self.posterior_lo,
            "posterior": self.posterior,
            "borne_erreur_certifiee": self.borne_erreur,
            "decision": self.decision,
            "mode": self.mode,
        }


class TraceurOracle:
    """Collecte les traces d'un passage d'inférence. Zéro état global :
    une instance par exécution, jamais de singleton."""

    def __init__(self) -> None:
        self.entrees: List[TraceEntree] = []

    def enregistrer(self, moteur: str, cible: str, symbole: str,
                    prior: float, termes_evidence: dict,
                    posterior: Optional[float] = None,
                    posterior_hi: Optional[float] = None,
                    posterior_lo: Optional[float] = None,
                    borne_erreur: Optional[float] = None,
                    decision: str = "", mode: str = "flottant") -> TraceEntree:
        entree = TraceEntree(
            horodatage=datetime.now(timezone.utc).isoformat(),
            moteur=moteur, cible=cible, symbole=symbole, prior=prior,
            termes_evidence=dict(termes_evidence),
            posterior=posterior, posterior_hi=posterior_hi,
            posterior_lo=posterior_lo, borne_erreur=borne_erreur,
            decision=decision, mode=mode,
        )
        self.entrees.append(entree)
        return entree

    def filtrer(self, symbole: str) -> List[TraceEntree]:
        """Toutes les traces d'un symbole/hypothèse (comparaison exacte)."""
        return [e for e in self.entrees if e.symbole == symbole]

    def vers_json(self) -> dict:
        """Enveloppe JSON-sérialisable, embarquable dans un snapshot."""
        return {
            "outil": "phi oracle",
            "version_phi": VERSION,
            "nb_traces": len(self.entrees),
            "traces": [e.vers_dict() for e in self.entrees],
            "limites": LIMITE_ORACLE,
        }


def rendre_oracle_console(traces: List[TraceEntree]) -> str:
    """Chaîne de raisonnement lisible : prior → évidences → postérieur → action."""
    lignes = [
        "╔════════════════════════════════════════════════════════════════════╗",
        "║              PHI-COMPLEXITY — TRACE D'ORACLE  🔮                   ║",
        "╚════════════════════════════════════════════════════════════════════╝",
        "",
    ]
    if not traces:
        lignes.append("  (aucune trace — rien n'a été inféré)")
        return "\n".join(lignes)
    for i, t in enumerate(traces, 1):
        lignes.append(f"  ┌─ TRACE {i} : {t.symbole}  [{t.moteur} · {t.cible}]")
        lignes.append(f"  │  prior    : {t.prior:.6f}")
        if t.termes_evidence:
            termes = "  ".join(f"{k}={v:.4f}" for k, v in t.termes_evidence.items())
            lignes.append(f"  │  évidences: {termes}")
        if t.posterior is not None:
            lignes.append(f"  │  postérieur: {t.posterior:.6f}", )
        if t.borne_erreur is not None:
            lignes.append(f"  │  borne d'erreur certifiée (EFT) : {t.borne_erreur:.3e}")
        else:
            lignes.append("  │  borne d'erreur : non certifiée (mode flottant)")
        lignes.append(f"  │  décision : {t.decision}")
        lignes.append(f"  │  mode     : {t.mode} · {t.horodatage}")
        lignes.append("  └" + "─" * 68)
        lignes.append("")
    lignes.append("  LIMITES : " + LIMITE_ORACLE)
    return "\n".join(lignes)
