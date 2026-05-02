"""Génération des fiches mémoire par repo et de la carte globale index.md (Mermaid + Obsidian)."""
import re
from datetime import datetime
from pathlib import Path

SYNAPTEX_DIR = Path.home() / ".synaptex"
MEMORY_DIR = SYNAPTEX_DIR / "memory"
PROJECTS_DIR = SYNAPTEX_DIR / "projects"

STACK_PATTERNS = {
    "Python": re.compile(r"\bpython\b|\bpip\b|\.py\b|django|flask|fastapi|click|pytest", re.I),
    "Node/JS": re.compile(r"\bnode\b|\bnpm\b|\byarn\b|typescript|react|vue|nextjs|bun", re.I),
    "Rust": re.compile(r"\bcargo\b|\brust\b|\.rs\b|tokio|actix|axum", re.I),
    "Go": re.compile(r"\bgo\b|\bgolang\b|\.go\b|goroutine|go\.mod", re.I),
    "C/C++": re.compile(r"\bcmake\b|\bmake\b|\.cpp\b|\.c\b|gcc|clang|arduino", re.I),
    "GDScript": re.compile(r"\bgodot\b|gdscript|\.gd\b|\.tscn\b", re.I),
    "Docker": re.compile(r"\bdocker\b|dockerfile|docker-compose", re.I),
    "Nix/NixOS": re.compile(r"\bnix\b|nixos|flake\.nix|home-manager", re.I),
    "ESP32": re.compile(r"\besp32\b|\besp-idf\b|esphome|micropython", re.I),
    "Raspberry Pi": re.compile(r"\braspberry\b|\brpi\b|\bpi\b.*gpio|gpio.*\bpi\b", re.I),
}

DEP_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _detect_stack(content: str) -> list[str]:
    return [name for name, rx in STACK_PATTERNS.items() if rx.search(content)]


def _detect_deps(content: str) -> list[str]:
    deps = set()
    for m in DEP_PATTERN.finditer(content):
        ref = m.group(1)
        if ref:
            d = ref.strip()
            if d:
                deps.add(d)
    return sorted(deps)


def generate_memory_sheet(repo_name: str, claude_md_content: str, last_updated: str = "") -> Path:
    """Génère ~/.synaptex/memory/<repo>.md à partir du contenu CLAUDE.md."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    stack = _detect_stack(claude_md_content)
    deps = _detect_deps(claude_md_content)
    ts = last_updated or datetime.now().isoformat(timespec="seconds")

    # Extraire la première ligne non-vide comme description
    description = ""
    for line in claude_md_content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            description = stripped[:200]
            break

    lines = [
        f"# {repo_name}",
        "",
        f"**Description** : {description}",
        f"**Stack** : {', '.join(stack) if stack else 'non détectée'}",
        f"**Dernière MàJ** : {ts}",
        "",
    ]

    if deps:
        lines += ["## Dépendances inter-projets", ""]
        lines += [f"- [[{d}]]" for d in deps]
        lines.append("")

    lines += [
        "## Extrait CLAUDE.md",
        "",
        "```",
        claude_md_content[:1000].strip(),
        "```" if len(claude_md_content) > 1000 else "",
    ]

    dest = MEMORY_DIR / f"{repo_name}.md"
    dest.write_text("\n".join(lines))
    return dest


def generate_index(projects_dir: Path | None = None) -> Path:
    """Génère ~/.synaptex/index.md avec graphe Mermaid + liste des projets."""
    if projects_dir is None:
        projects_dir = PROJECTS_DIR

    repos: dict[str, dict] = {}
    for md_file in projects_dir.rglob("CLAUDE.md"):
        repo = md_file.parts[len(projects_dir.parts)]
        content = md_file.read_text(errors="replace")
        mode_file = projects_dir / repo / ".synaptex_mode"
        mode = mode_file.read_text().strip() if mode_file.exists() else "git"
        repos[repo] = {
            "stack": _detect_stack(content),
            "deps": _detect_deps(content),
            "mode": mode,
        }

    # Compléter avec les fiches mémoire existantes
    for fiche in MEMORY_DIR.glob("*.md"):
        name = fiche.stem
        if name not in repos:
            repos[name] = {"stack": [], "deps": []}

    lines = [
        "# Synaptex — Carte globale des projets",
        f"_Généré le {datetime.now().isoformat(timespec='seconds')}_",
        "",
        "## Graphe des dépendances",
        "",
        "```mermaid",
        "graph TD",
    ]

    node_ids: dict[str, str] = {}
    for i, repo in enumerate(sorted(repos)):
        nid = f"N{i}"
        node_ids[repo] = nid
        lines.append(f'    {nid}["{repo}"]')

    for repo, info in sorted(repos.items()):
        for dep in info["deps"]:
            if dep in node_ids:
                lines.append(f"    {node_ids[repo]} --> {node_ids[dep]}")

    lines += [
        "```",
        "",
        "## Projets",
        "",
    ]

    for repo, info in sorted(repos.items()):
        badge = " 🗂 vault" if info.get("mode") == "vault" else " git"
        stack_str = f" — {', '.join(info['stack'])}" if info["stack"] else ""
        deps_str = f" | dépend de : {', '.join(f'[[{d}]]' for d in info['deps'])}" if info["deps"] else ""
        lines.append(f"- [[{repo}]]{badge}{stack_str}{deps_str}")

    dest = SYNAPTEX_DIR / "index.md"
    dest.write_text("\n".join(lines) + "\n")
    return dest
