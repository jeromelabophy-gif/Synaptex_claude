"""Génération du bloc contexte injectable via stdout — $(synaptex context ppg esp32)."""
from pathlib import Path

SYNAPTEX_DIR = Path.home() / ".synaptex"
MEMORY_DIR = SYNAPTEX_DIR / "memory"
INDEX_FILE = SYNAPTEX_DIR / "index.md"
PROJECTS_DIR = SYNAPTEX_DIR / "projects"


def get_context(projects: tuple[str, ...] = ()) -> str:
    """
    Retourne un bloc texte prêt à être injecté dans un prompt Claude.
    Sans arguments : index global + toutes les fiches.
    Avec arguments : filtre sur les projets nommés.
    """
    parts: list[str] = []

    parts.append("=" * 60)
    parts.append("SYNAPTEX CONTEXT — DO NOT MODIFY")
    parts.append("=" * 60)
    parts.append("")

    # Index global (toujours inclus)
    if INDEX_FILE.exists():
        parts.append("## INDEX GLOBAL")
        parts.append("")
        parts.append(INDEX_FILE.read_text())
        parts.append("")

    # Fiches mémoire
    fiches = sorted(MEMORY_DIR.glob("*.md")) if MEMORY_DIR.exists() else []
    if projects:
        # Filtre : chercher les projets demandés (correspondance partielle tolérée)
        fiches = [
            f for f in fiches
            if any(p.lower() in f.stem.lower() for p in projects)
        ]

    if fiches:
        parts.append("## FICHES MÉMOIRE")
        parts.append("")
        for fiche in fiches:
            parts.append(f"### {fiche.stem}")
            parts.append(fiche.read_text())
            parts.append("")

    # CLAUDE.md des projets ciblés (contenu complet)
    if projects:
        for project in projects:
            candidates = list(PROJECTS_DIR.glob(f"*{project}*/CLAUDE.md"))
            for md_file in candidates:
                repo = md_file.parts[len(PROJECTS_DIR.parts)]
                parts.append(f"## CLAUDE.md — {repo}")
                parts.append("")
                parts.append(md_file.read_text(errors="replace"))
                parts.append("")

    parts.append("=" * 60)
    parts.append("END SYNAPTEX CONTEXT")
    parts.append("=" * 60)

    return "\n".join(parts)
