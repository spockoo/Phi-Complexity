"""
registre_observations.py — Registre d'observations : le veto humain consigné.

Directive unique (Tomy) : démontrer formellement la conjecture de
Navier-Stokes suivant les critères de l'institut Clay.
Troisième directive (Tomy, 2026-10-01) : développer phi-complexity
librement SOUS CONDITION que ça serve la directive unique.

Règle CONSTITUTIONNELLE (garde testée dans tests/test_garde_non_prescriptif.py) :
l'instrument apprend le VOCABULAIRE des situations de veto, jamais la
DÉCISION. Ce module consigne (observation → décision humaine + motif +
date) ; il ne prédit ni ne recommande aucun veto — aucune fonction de ce
module ne porte sur l'opportunité d'un veto. Le veto reste le garde-fou
non mécanisable de Tomy.

Format d'entrée (JSON) :
{
  "date": "2026-10-03T10:30:00+00:00",
  "observation": {"kind": ..., "fichier": ..., "ligne": ..., "nom": ...},
  "veto": true | false,
  "motif": "phrase du décideur humain (≥10 caractères)"
}
"""

import json
import os
from datetime import datetime, timezone

#: Longueur minimale d'un motif (anti-remplissage).
MOTIF_MIN_LONGUEUR = 10


def _chemin_defaut() -> str:
    return os.path.join(os.path.expanduser("~"),
                        ".phi-complexity-registre-observations.json")


def charger(chemin: str = None) -> list:
    """Charge le registre (liste d'entrées) ; [] si absent."""
    chemin = chemin or _chemin_defaut()
    if not os.path.isfile(chemin):
        return []
    with open(chemin, encoding="utf-8") as fh:
        donnees = json.load(fh)
    return donnees if isinstance(donnees, list) else []


def _sauver(entrees: list, chemin: str) -> None:
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entrees, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, chemin)


def consigner(observation: dict, veto: bool, motif: str,
              chemin: str = None) -> dict:
    """Consigne une décision humaine de veto sur une observation.

    - observation : dict tel que produit par le radar (kind, fichier,
      ligne, nom, ...) ;
    - veto : True = veto exercé, False = pas de veto ;
    - motif : phrase du décideur, ≥ MOTIF_MIN_LONGUEUR caractères.

    Lève ValueError si le motif est trop court. Retourne l'entrée
    consignée (avec date ISO). Saisie manuelle : c'est l'humain qui
    décide, l'instrument ne fait que consigner.
    """
    if not isinstance(motif, str) or len(motif.strip()) < MOTIF_MIN_LONGUEUR:
        raise ValueError(
            f"motif trop court (min {MOTIF_MIN_LONGUEUR} caractères) : "
            "une décision de veto s'explique ou ne se consigne pas")
    if not isinstance(observation, dict) or "nom" not in observation:
        raise ValueError("observation invalide : dict avec au moins 'nom'")
    chemin = chemin or _chemin_defaut()
    entree = {
        "date": datetime.now(timezone.utc).isoformat(),
        "observation": dict(observation),
        "veto": bool(veto),
        "motif": motif.strip(),
    }
    entrees = charger(chemin)
    entrees.append(entree)
    _sauver(entrees, chemin)
    return entree


def lister(chemin: str = None) -> list:
    """Toutes les entrées consignées, dans l'ordre chronologique."""
    return charger(chemin)
