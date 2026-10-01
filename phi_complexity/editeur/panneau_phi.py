"""
editeur/panneau_phi.py — Audit phi d'un tampon d'édition.

`auditer_tampon` écrit le contenu du tampon dans un fichier temporaire
portant LA MÊME extension que le fichier édité (les analyseurs phi
travaillent depuis un chemin), lance `auditer()` dessus, puis supprime
le temporaire. Ne lève jamais : toute erreur est retournée sous la clé
`"erreur"`.
"""
import os
import tempfile
from typing import Optional

from ..langs import est_fichier_supporte


NB_ANNOTATIONS_AFFICHEES = 5


def _resultat_erreur(message: str) -> dict:
    """Enveloppe d'erreur normalisée : jamais d'exception vers l'appelant."""
    return {"erreur": message}


def auditer_tampon(tampon, chemin: str, lang: Optional[str] = None) -> dict:
    """
    Audite le contenu courant du tampon avec phi-complexity.

    Retourne :
        {radiance, statut_gnostique, oudjat: {nom, ligne, complexite}|None,
         nb_anomalies, annotations: [{ligne, niveau, message}] (top 5)}
    ou {"erreur": ...} si le langage n'est pas supporté ou l'audit échoue.
    """
    from .. import auditer  # import local : évite tout cycle

    if lang is None and not est_fichier_supporte(chemin):
        return _resultat_erreur(
            f"langage non supporté pour '{os.path.basename(chemin)}' — "
            "l'édition reste disponible, l'audit phi est désactivé."
        )
    extension = os.path.splitext(chemin)[1]
    chemin_temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=extension or ".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(tampon.texte())
            chemin_temp = f.name
        metriques = auditer(chemin_temp, lang=lang)
    except Exception as e:
        return _resultat_erreur(f"échec de l'audit phi : {e}")
    finally:
        if chemin_temp and os.path.isfile(chemin_temp):
            try:
                os.unlink(chemin_temp)
            except OSError:
                pass
    if not isinstance(metriques, dict) or "radiance" not in metriques:
        return _resultat_erreur("résultat d'audit inattendu (clés manquantes).")
    oudjat = metriques.get("oudjat")
    annotations = metriques.get("annotations") or []
    return {
        "radiance": metriques["radiance"],
        "statut_gnostique": metriques.get("statut_gnostique"),
        "oudjat": (
            {"nom": oudjat["nom"], "ligne": oudjat["ligne"],
             "complexite": oudjat["complexite"]}
            if oudjat
            else None
        ),
        "nb_anomalies": len(annotations),
        "annotations": [
            {"ligne": a.get("ligne"), "niveau": a.get("niveau"),
             "message": a.get("message")}
            for a in annotations[:NB_ANNOTATIONS_AFFICHEES]
        ],
    }
