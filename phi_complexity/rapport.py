"""
rapport.py — Génération du Rapport de Radiance Premium (Markdown + Console).
Style "CodeRabbit × Bibliothèque Céleste".
"""
import datetime
import json
from .core import VERSION


class GenerateurRapport:
    """Transforme un dictionnaire de métriques en rapport premium."""

    def __init__(self, metriques: dict):
        self.m = metriques

    # ──────────────────────────────────────────────
    # RECOMMANDATIONS CHIRURGICALES
    # ──────────────────────────────────────────────

    def prescriptions(self) -> list:
        """Génère des recommandations d'action concrètes et chirurgicales pour le développeur."""
        m = self.m
        conseils = []

        # 1. Diagnostic de l'Oudjat (foyer de complexité)
        if m.get("oudjat"):
            o = m["oudjat"]
            if o.get("phi_ratio", 1.0) > 2.5:
                conseils.append(
                    f"✦ Décomposition de l'Oudjat : '{o['nom']}' (L{o['ligne']}) concentre "
                    f"{o['phi_ratio']:.2f}x la complexité moyenne (cible φ ≈ 1.62). "
                    "Scindez cet organe en 2 ou 3 sous-fonctions pures."
                )

        # 2. Variance de Lilith et Spectre
        rel_var = m.get("lilith_rel_variance", 0.0)
        if rel_var > 0.618:
            conseils.append(
                f"⚖ Équilibrage structurel : Variance relative ({rel_var:.3f}) > φ⁻¹ (0.618). "
                "Rééquilibrez la charge algorithmique entre les différents blocs."
            )
        kurt = m.get("lilith_kurtosis", 3.0)
        if kurt > 6.0:
            conseils.append(
                f"⚡ Singularité détectée (Kurtosis = {kurt:.2f} > 6.0) : Présence d'une queue lourde critique. "
                "Un bloc ultra-complexe isole la structure d'ensemble."
            )

        # 3. Entropie de Shannon
        h_norm = m.get("shannon_entropy_norm", 0.0)
        if 0 < h_norm < 0.25 and m.get("nb_fonctions", 0) > 2:
            conseils.append(
                "🌊 Rupture d'entropie : Structure proche d'un God-object monolithique. "
                "Déléguez davantage de logique aux fonctions satellites."
            )

        # 4. Tailles de Fibonacci
        fib_moy = m.get("fibonacci_distance_moyenne", 0.0)
        if fib_moy > 1.5:
            conseils.append(
                f"░ Alignement harmonique : Écart moyen à Fibonacci = {fib_moy:.2f}. "
                "Calibrez vos tailles de fonctions vers la suite naturelle (8, 13, 21, 34, 55 lignes)."
            )

        # 5. Anomalies
        annotations = m.get("annotations", [])
        categories = {a.get("categorie") for a in annotations}
        if "LILITH" in categories:
            conseils.append("🔴 Boucles imbriquées : Transformez les itérations imbriquées en fonctions auxiliaires ou flux d'itération.")
        if "SUTURE" in categories:
            conseils.append("🟡 Gestion de ressource : Encadrez systématiquement les descripteurs dans un bloc de gestionnaire de contexte (RAII).")
        if "SOUVERAINETE" in categories:
            conseils.append("🔵 Arithmétique d'arguments : Regroupez les paramètres excédant 5 dans une structure ou un enregistrement typé.")

        return conseils

    # ──────────────────────────────────────────────
    # RENDU CONSOLE (ASCII Premium)
    # ──────────────────────────────────────────────

    def console(self) -> str:
        """Sortie console premium avec barres visuelles."""
        m = self.m
        score = m["radiance"]
        barre = self._barre(score)
        phi_r = m.get("phi_ratio", 1.0)
        delta = m.get("phi_ratio_delta", 0.0)
        phi_icon = "✦" if delta < 0.15 else "◈" if delta < 0.5 else "░"

        lignes = [
            "╔══════════════════════════════════════════════════╗",
            "║      PHI-COMPLEXITY — AUDIT DE RADIANCE          ║",
            "╚══════════════════════════════════════════════════╝",
            "",
            f"  📄 Fichier : {m['fichier']}",
            f"  🌐 Langage : {m.get('langage', 'python')}",
            f"  📅 Date    : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            f"  ☼  RADIANCE     : {barre}  {score} / 100",
            f"  ⚖  LILITH       : {m['lilith_variance']:.2f}  (Rel: {m.get('lilith_rel_variance', 0.0):.3f}, Skew: {m.get('lilith_skewness', 0.0):.2f}, Kurt: {m.get('lilith_kurtosis', 3.0):.2f})",
            f"  🛡  ANTIFRAGILE  : {m.get('antifragilite', 0.0):.3f}  ({m.get('statut_antifragile', 'RÉSISTANT ◈')})",
            f"  🌊 ENTROPIE     : {m['shannon_entropy']:.3f} bits  (Norm: {m.get('shannon_entropy_norm', 0.0):.3f})",
            f"  {phi_icon}  PHI-RATIO    : {phi_r:.3f}  (idéal: φ = 1.618, Δ={delta:.3f})",
            f"  ζ  ZETA-SCORE   : {m['zeta_score']:.4f}  (Résonance globale)",
            "",
        ]

        # Statut gnostique
        lignes.append(f"  STATUT : {m['statut_gnostique']}")
        lignes.append("")

        # Oudjat
        if m.get("oudjat"):
            o = m["oudjat"]
            lignes.append(
                f"  🔎 OUDJAT : '{o['nom']}' (Ligne {o['ligne']}, "
                f"Complexité: {o['complexite']}, φ-ratio: {o['phi_ratio']})"
            )
            lignes.append("")

        # Annotations
        annotations = m.get("annotations", [])
        if annotations:
            lignes.append(f"  ⚠  SUTURES IDENTIFIÉES ({len(annotations)}) :")
            for ann in annotations:
                icon = "🔴" if ann["niveau"] == "CRITICAL" else "🟡" if ann["niveau"] == "WARNING" else "🔵"
                lignes.append(f"  {icon} Ligne {ann['ligne']} [{ann['categorie']}] : {ann['message']}")
                lignes.append(f"     >> {ann['extrait']}")
        else:
            lignes.append("  ✦  Aucune rupture de radiance majeure détectée.")

        # Prescriptions d'équilibre
        conseils = self.prescriptions()
        if conseils:
            lignes.append("")
            lignes.append("  💡 PRESCRIPTIONS D'ÉQUILIBRE :")
            for c in conseils:
                lignes.append(f"    {c}")

        lignes.append("")
        lignes.append("  ─────────────────────────────────────────────────")
        lignes.append(f"  Ancré dans le Morphic Phi Framework — φ-Meta 2026")

        return "\n".join(lignes)

    # ──────────────────────────────────────────────
    # RENDU MARKDOWN (Premium)
    # ──────────────────────────────────────────────

    def markdown(self) -> str:
        """Rapport Markdown complet, style Bibliothèque Céleste."""
        m = self.m
        score = m["radiance"]
        barre = self._barre_md(score)
        date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        rapport = f"# ☼ RAPPORT DE RADIANCE : {m['fichier']}\n\n"
        rapport += f"**Langage** : {m.get('langage', 'python')}  \n"
        rapport += f"**Date de l'Audit** : {date}  \n"
        rapport += f"**Statut Gnostique** : **{m['statut_gnostique']}**\n\n"

        # Section 1 — Score
        rapport += "## 1. INDICE DE RADIANCE\n\n"
        rapport += f"**Score : {score} / 100**\n\n"
        rapport += f"`[{barre}]` {score}%\n\n"

        # Section 2 — Métriques brutes
        rapport += "## 2. MÉTRIQUES SOUVERAINES\n\n"
        rapport += "| Métrique | Valeur | Interprétation |\n"
        rapport += "|---|---|---|\n"
        rapport += f"| **Variance de Lilith** | {m['lilith_variance']} (relative: {m.get('lilith_rel_variance', 0.0)}) | Instabilité structurelle (invariant d'échelle) |\n"
        rapport += f"| **Spectre de Lilith (Skew/Kurt)** | Skew: {m.get('lilith_skewness', 0.0)} / Kurt: {m.get('lilith_kurtosis', 3.0)} | Asymétrie et détection de queues lourdes / singularités |\n"
        rapport += f"| **Capacité Antifragile (α_φ)** | {m.get('antifragilite', 0.0)} ({m.get('statut_antifragile', '—')}) | Réserve d'adaptation aux chocs et refactorisations |\n"
        rapport += f"| **Entropie de Shannon** | {m['shannon_entropy']} bits (normalisée: {m.get('shannon_entropy_norm', 0.0)}) | Densité informationnelle relative |\n"
        rapport += f"| **φ-Ratio** | {m['phi_ratio']} (Δ={m['phi_ratio_delta']}) | Idéal: 1.618 |\n"
        rapport += f"| **Zeta-Score** | {m['zeta_score']} | Résonance globale |\n"
        rapport += f"| **Distance Fibonacci** | {m['fibonacci_distance']} (moyenne: {m.get('fibonacci_distance_moyenne', 0.0)}) | Éloignement des tailles naturelles |\n"
        rapport += f"| **Fonctions analysées** | {m['nb_fonctions']} | — |\n"
        rapport += f"| **Ratio commentaires** | {m['ratio_commentaires']} | Densité de sagesse |\n\n"

        # Section 3 — Oudjat
        if m.get("oudjat"):
            o = m["oudjat"]
            rapport += "## 3. IDENTIFICATION DE L'OUDJAT\n\n"
            rapport += f"La fonction la plus 'chargée' est **`{o['nom']}`** (Ligne {o['ligne']}).\n\n"
            rapport += f"- Pression : **{o['complexite']}** unités de complexité\n"
            rapport += f"- Taille   : **{o['nb_lignes']}** lignes\n"
            rapport += f"- φ-Ratio  : **{o['phi_ratio']}** (idéal: 1.618)\n\n"

        # Section 4 — Audit Fractal
        rapport += "## 4. REVUE DE DÉTAIL (AUDIT FRACTAL)\n\n"
        annotations = m.get("annotations", [])
        if not annotations:
            rapport += "☼ Aucune rupture de radiance majeure détectée au niveau micro.\n\n"
        else:
            rapport += f"Phidélia a identifié **{len(annotations)}** zones nécessitant une suture :\n\n"
            for ann in annotations:
                niveau_md = (
                    "CAUTION" if ann["niveau"] == "CRITICAL"
                    else "WARNING" if ann["niveau"] == "WARNING"
                    else "NOTE"
                )
                rapport += f"> [!{niveau_md}]\n"
                rapport += f"> **Ligne {ann['ligne']}** `[{ann['categorie']}]` : {ann['message']}\n"
                rapport += f"> `>> {ann['extrait']}`\n\n"

        # Section 5 — Prescriptions
        conseils = self.prescriptions()
        if conseils:
            rapport += "## 5. PRESCRIPTIONS DE REFACTORISATION ÉQUILIBRÉE\n\n"
            for c in conseils:
                rapport += f"- {c}\n"
            rapport += "\n"

        # Pied de page
        rapport += "---\n"
        rapport += "*phi-complexity — Morphic Phi Framework (φ-Meta) — Tomy Verreault, 2026*\n"
        return rapport

    # ──────────────────────────────────────────────
    # RENDU JSON & SARIF (CI/CD)
    # ──────────────────────────────────────────────

    def json(self) -> str:
        """Sortie JSON structurée pour intégrations CI/CD."""
        return json.dumps(self.m, ensure_ascii=False, indent=2)

    def sarif(self) -> str:
        """Produit un rapport au standard OASIS SARIF v2.1.0 (GitHub Code Scanning, VS Code)."""
        rules = [
            {"id": "PHI-LILITH", "name": "BoucleImbriquee", "shortDescription": {"text": "Boucle trop imbriquée générant une forte variance"}},
            {"id": "PHI-SUTURE", "name": "CycleDeVieRessource", "shortDescription": {"text": "Ressource ouverte sans gestionnaire de contexte (RAII)"}},
            {"id": "PHI-FIBONACCI", "name": "TailleFonction", "shortDescription": {"text": "Fonction s'éloignant des tailles naturelles de Fibonacci"}},
            {"id": "PHI-SOUVERAINETE", "name": "AriteFonction", "shortDescription": {"text": "Fonction recevant trop d'arguments formels (> 5)"}},
            {"id": "PHI-OUDJAT", "name": "OudjatHypertrophie", "shortDescription": {"text": "Fonction dominante concentrant un ratio de complexité excessif"}},
        ]
        results = []
        for ann in self.m.get("annotations", []):
            cat = ann.get("categorie", "SUTURE")
            rule_id = f"PHI-{cat}"
            level = "error" if ann.get("niveau") == "CRITICAL" else "warning" if ann.get("niveau") == "WARNING" else "note"
            results.append({
                "ruleId": rule_id,
                "level": level,
                "message": {"text": ann.get("message", "")},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": self.m.get("fichier", "")},
                        "region": {"startLine": max(1, int(ann.get("ligne", 1)))}
                    }
                }]
            })

        if self.m.get("oudjat") and self.m["oudjat"].get("phi_ratio", 1.0) > 3.0:
            o = self.m["oudjat"]
            results.append({
                "ruleId": "PHI-OUDJAT",
                "level": "warning",
                "message": {"text": f"Oudjat '{o.get('nom')}' concentre un phi-ratio de {o.get('phi_ratio'):.2f} (seuil cible: 1.62)."},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": self.m.get("fichier", "")},
                        "region": {"startLine": max(1, int(o.get("ligne", 1)))}
                    }
                }]
            })

        sarif_doc = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "phi-complexity",
                        "version": VERSION,
                        "informationUri": "https://github.com/spockoo/phi-complexity",
                        "rules": rules
                    }
                },
                "results": results
            }]
        }
        return json.dumps(sarif_doc, ensure_ascii=False, indent=2)
        return json.dumps(self.m, ensure_ascii=False, indent=2)

    def sauvegarder_markdown(self, chemin: str) -> str:
        """Sauvegarde le rapport Markdown dans un fichier."""
        contenu = self.markdown()
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(contenu)
        return chemin

    # ──────────────────────────────────────────────
    # UTILITAIRES INTERNES
    # ──────────────────────────────────────────────

    def _barre(self, score: float, largeur: int = 20) -> str:
        """Barre de progression ASCII pour le terminal."""
        rempli = int(score / 100 * largeur)
        vide = largeur - rempli
        return "█" * rempli + "░" * vide

    def _barre_md(self, score: float, largeur: int = 10) -> str:
        """Barre de progression pour Markdown (blocs compacts)."""
        rempli = int(score / 100 * largeur)
        vide = largeur - rempli
        return "█" * rempli + "░" * vide
