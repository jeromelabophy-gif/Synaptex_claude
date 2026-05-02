"""Multi-git bridge — sync CLAUDE.md files to ~/.synaptex/projects/

Supported git providers:
  forgejo / gitea  — Forgejo/Gitea API v1 (default)
  github           — GitHub REST API v3
  gitlab           — GitLab REST API v4

Set FORGE_TYPE in ~/.synaptex/.env (default: forgejo).
"""
import hashlib
import logging
import re
import time
from datetime import datetime
from fnmatch import fnmatch as _fnmatch
from pathlib import Path

import requests


def _match_patterns(path: str, patterns: list[str]) -> bool:
    """Return True if the filename (last component of path) matches any pattern."""
    filename = path.split("/")[-1]
    return any(_fnmatch(filename, pat) for pat in patterns)


SYNAPTEX_DIR = Path.home() / ".synaptex"
PROJECTS_DIR = SYNAPTEX_DIR / "projects"
SYNC_LOG = SYNAPTEX_DIR / "sync.log"

# Tailscale IP range (100.x.x.x) removed — not universally applicable
# Chaque règle : (pattern compilé, raison courte à afficher)
_SECRET_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"FORGE_TOKEN|FORGEJO_TOKEN|GITHUB_TOKEN|GITLAB_TOKEN", re.IGNORECASE), "token git"),
    (re.compile(r"AWS_[A-Z_]+", re.IGNORECASE), "credential AWS"),
    (re.compile(r"PASSWORD", re.IGNORECASE), "mot de passe"),
    (re.compile(r"SECRET", re.IGNORECASE), "variable secrète"),
    (re.compile(r"API_KEY", re.IGNORECASE), "clé API"),
    (re.compile(r"PRIVATE_KEY", re.IGNORECASE), "clé privée"),
    (re.compile(r"BEGIN\s+(RSA|EC|OPENSSH|PGP)\s+PRIVATE", re.IGNORECASE), "clé privée PEM"),
    (re.compile(r"192\.168\.|10\.\d+\.\d+\.|172\.(1[6-9]|2\d|3[01])\.", re.IGNORECASE), "adresse IP locale"),
]
# Compat avec tout code qui importerait SECRET_PATTERNS directement
SECRET_PATTERNS = re.compile(
    "|".join(p.pattern for p, _ in _SECRET_RULES), re.IGNORECASE
)

logger = logging.getLogger(__name__)


def _log(msg: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"{ts}  {msg}\n"
    SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    SYNC_LOG.write_text(SYNC_LOG.read_text() + line if SYNC_LOG.exists() else line)
    logger.info(msg)


def _session(token: str, forge_type: str = "forgejo") -> requests.Session:
    s = requests.Session()
    if forge_type == "github":
        s.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
    elif forge_type == "gitlab":
        s.headers.update({"PRIVATE-TOKEN": token})
    else:  # forgejo / gitea
        s.headers.update({"Authorization": f"token {token}", "Content-Type": "application/json"})
    return s


# ---------------------------------------------------------------------------
# Forgejo / Gitea
# ---------------------------------------------------------------------------

def _forgejo_list_repos(base_url: str, token: str, user: str) -> list[dict]:
    s = _session(token, "forgejo")
    repos, page = [], 1
    while True:
        r = s.get(
            f"{base_url}/api/v1/repos/search",
            params={"limit": 50, "page": page, "token": token},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def _forgejo_default_branch(base_url: str, session: requests.Session, owner: str, repo: str) -> str:
    r = session.get(f"{base_url}/api/v1/repos/{owner}/{repo}", timeout=10)
    r.raise_for_status()
    return r.json().get("default_branch", "main")


def _forgejo_find_claude_mds(base_url: str, token: str, owner: str, repo: str,
                              patterns: list[str] | None = None) -> list[str]:
    _patterns = patterns or ["CLAUDE.md"]
    s = _session(token, "forgejo")
    branch = _forgejo_default_branch(base_url, s, owner, repo)
    r = s.get(
        f"{base_url}/api/v1/repos/{owner}/{repo}/git/trees/{branch}",
        params={"recursive": "true"},
        timeout=20,
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    tree = r.json().get("tree", [])
    return [item["path"] for item in tree if _match_patterns(item.get("path", ""), _patterns)]


def _forgejo_download(base_url: str, token: str, owner: str, repo: str, path: str) -> str:
    s = _session(token, "forgejo")
    branch = _forgejo_default_branch(base_url, s, owner, repo)
    r = s.get(
        f"{base_url}/api/v1/repos/{owner}/{repo}/raw/{path}",
        params={"ref": branch, "token": token},
        timeout=15,
    )
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _github_list_repos(token: str) -> list[dict]:
    s = _session(token, "github")
    repos, page = [], 1
    while True:
        r = s.get(
            "https://api.github.com/user/repos",
            params={"per_page": 100, "page": page, "affiliation": "owner"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def _github_find_claude_mds(token: str, owner: str, repo: str,
                             patterns: list[str] | None = None) -> list[str]:
    _patterns = patterns or ["CLAUDE.md"]
    s = _session(token, "github")
    r = s.get(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD",
        params={"recursive": "1"},
        timeout=20,
    )
    if r.status_code in (404, 409):
        return []
    r.raise_for_status()
    tree = r.json().get("tree", [])
    return [item["path"] for item in tree if _match_patterns(item.get("path", ""), _patterns)]


def _github_download(token: str, owner: str, repo: str, path: str) -> str:
    s = _session(token, "github")
    r = s.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        timeout=15,
    )
    r.raise_for_status()
    import base64
    data = r.json()
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------

def _gitlab_list_repos(base_url: str, token: str) -> list[dict]:
    s = _session(token, "gitlab")
    base = base_url.rstrip("/")
    repos, page = [], 1
    while True:
        r = s.get(
            f"{base}/api/v4/projects",
            params={"owned": "true", "per_page": 100, "page": page},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def _gitlab_find_claude_mds(base_url: str, token: str, project_id: int,
                             patterns: list[str] | None = None) -> list[str]:
    _patterns = patterns or ["CLAUDE.md"]
    s = _session(token, "gitlab")
    base = base_url.rstrip("/")
    r = s.get(
        f"{base}/api/v4/projects/{project_id}/repository/tree",
        params={"recursive": "true", "per_page": 100},
        timeout=20,
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return [item["path"] for item in r.json() if _match_patterns(item.get("path", ""), _patterns)]


def _gitlab_download(base_url: str, token: str, project_id: int, path: str) -> str:
    import urllib.parse
    s = _session(token, "gitlab")
    base = base_url.rstrip("/")
    encoded = urllib.parse.quote(path, safe="")
    r = s.get(
        f"{base}/api/v4/projects/{project_id}/repository/files/{encoded}/raw",
        params={"ref": "HEAD"},
        timeout=15,
    )
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------------------
# Local (no API — scan local git repos)
# ---------------------------------------------------------------------------

def _local_sync(
    repos_path: str,
    dry_run: bool,
    include_patterns: list[str] | None = None,
    exclude: list[str] | None = None,
    only: str | None = None,
) -> dict:
    """Scan LOCAL_REPOS_PATH for git repos and read matching files directly."""
    base = Path(repos_path).expanduser().resolve()
    patterns = include_patterns or ["CLAUDE.md"]
    _exclude = exclude or []
    result: dict = {"synced": [], "skipped": [], "warnings": []}

    if not base.exists():
        _log(f"  ✗ LOCAL_REPOS_PATH does not exist: {base}")
        return result

    # Find all git repos (directories containing .git/)
    git_dirs = [p.parent for p in base.rglob(".git") if p.is_dir()]
    _log(f"  {len(git_dirs)} git repos found in {base}")

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    for repo_dir in sorted(git_dirs):
        name = repo_dir.name
        if only and name != only:
            continue
        if name in _exclude:
            result["skipped"].append(name)
            continue
        md_files: list[Path] = []
        for pat in patterns:
            md_files.extend(p for p in repo_dir.rglob(pat) if ".git" not in p.parts)
        md_files = sorted(set(md_files))

        if not md_files:
            result["skipped"].append(name)
            continue

        for md_file in md_files:
            path = str(md_file.relative_to(repo_dir))
            content = md_file.read_text(errors="replace")
            checksum = _sha256(content)
            warnings = _sanitise_check(content, name, path)
            result["warnings"].extend(warnings)

            if dry_run:
                _log(f"  [DRY] {name}/{path} sha256={checksum[:12]} warnings={len(warnings)}")
                result["synced"].append({"repo": name, "path": path, "checksum": checksum})
                continue

            dest = PROJECTS_DIR / name / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            (dest.parent / f"{dest.name}.sha256").write_text(checksum)
            _log(f"  ✓ {name}/{path}")
            for w in warnings:
                _log(f"  ℹ {w}")
            result["synced"].append({"repo": name, "path": path, "checksum": checksum})

    return result


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


_PLACEHOLDER_VALUES = {
    "", "xxx", "your-token-here", "your-username", "changeme",
    "todo", "tbd", "fixme", "...", "…", "none", "null",
}
_KV_RE = re.compile(r"^\s*[#-]?\s*([A-Z_][A-Z0-9_]*)\s*[:=]\s*(.*?)\s*$")


def _looks_like_placeholder(val: str) -> bool:
    """Heuristique : la valeur est-elle vide ou un placeholder évident ?"""
    v = val.strip().strip("\"'`")
    if not v or v.lower() in _PLACEHOLDER_VALUES:
        return True
    # <token>, ${TOKEN}, {{token}}, abc... (ellipsis)
    if v.startswith(("<", "${", "{{")) and v.endswith((">", "}", "}}")):
        return True
    if v.endswith(("...", "…")):
        return True
    return False


def _sanitise_check(content: str, repo: str, path: str) -> list[str]:
    """Heuristique douce : signale les valeurs qui *ressemblent* à un secret.
    Ne lève pas d'alerte sur la simple mention d'un nom (ex. `API_KEY` dans
    un commentaire) ni sur un placeholder (`KEY=your-token-here`).
    Synaptex donne un conseil — pas un audit de sécurité.
    """
    notices = []
    for line_no, line in enumerate(content.splitlines(), 1):
        for pattern, reason in _SECRET_RULES:
            if not pattern.search(line):
                continue
            # PEM block et IP locale : toujours signaler — sans format KV
            literal = "BEGIN" in pattern.pattern or "192" in pattern.pattern
            if not literal:
                m = _KV_RE.match(line)
                if not m or _looks_like_placeholder(m.group(2)):
                    break  # mention seule ou placeholder — on ignore
            notices.append(
                f"{repo}/{path}:{line_no} — {reason} potentielle : {line.strip()[:80]}"
            )
            break  # une seule raison par ligne
    return notices


# ---------------------------------------------------------------------------
# Public sync entry point
# ---------------------------------------------------------------------------

def sync_all(
    forge_url: str,
    token: str,
    user: str,
    dry_run: bool = False,
    forge_type: str = "forgejo",
    local_repos_path: str = "",
    include_patterns: list[str] | None = None,
    exclude: list[str] | None = None,
    only: str | None = None,
) -> dict:
    """Sync matching files to ~/.synaptex/projects/.

    Returns: {"synced": [...], "skipped": [...], "warnings": [...]}
    """
    patterns = include_patterns or ["CLAUDE.md"]
    _exclude = exclude or []
    _log(f"{'[DRY-RUN] ' if dry_run else ''}sync_all started — {forge_type}")

    if forge_type == "local":
        result = _local_sync(local_repos_path or "~/projects", dry_run, patterns, _exclude, only)
        _log(
            f"sync_all done — {len(result['synced'])} synced, "
            f"{len(result['skipped'])} skipped, {len(result['warnings'])} warnings"
        )
        return result

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    result = {"synced": [], "skipped": [], "warnings": []}

    if forge_type == "github":
        raw_repos = _github_list_repos(token)
        repos = [{"owner": r["owner"]["login"], "name": r["name"]} for r in raw_repos]
    elif forge_type == "gitlab":
        raw_repos = _gitlab_list_repos(forge_url, token)
        repos = [{"owner": r["namespace"]["path"], "name": r["path"], "_id": r["id"]} for r in raw_repos]
    else:  # forgejo / gitea
        raw_repos = _forgejo_list_repos(forge_url, token, user)
        repos = [{"owner": r["owner"]["login"], "name": r["name"]} for r in raw_repos]

    if only:
        repos = [r for r in repos if r["name"] == only]
    if _exclude:
        repos = [r for r in repos if r["name"] not in _exclude]

    _log(f"  {len(repos)} repos found")

    for repo in repos:
        owner = repo["owner"]
        name = repo["name"]

        if forge_type == "github":
            paths = _github_find_claude_mds(token, owner, name, patterns)
        elif forge_type == "gitlab":
            paths = _gitlab_find_claude_mds(forge_url, token, repo["_id"], patterns)
        else:
            paths = _forgejo_find_claude_mds(forge_url, token, owner, name, patterns)

        if not paths:
            result["skipped"].append(name)
            continue

        for path in paths:
            if forge_type == "github":
                content = _github_download(token, owner, name, path)
            elif forge_type == "gitlab":
                content = _gitlab_download(forge_url, token, repo["_id"], path)
            else:
                content = _forgejo_download(forge_url, token, owner, name, path)

            checksum = _sha256(content)
            dest = PROJECTS_DIR / name / path
            warnings = _sanitise_check(content, name, path)
            result["warnings"].extend(warnings)

            if dry_run:
                _log(f"  [DRY] {name}/{path} sha256={checksum[:12]} warnings={len(warnings)}")
                result["synced"].append({"repo": name, "path": path, "checksum": checksum})
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            (dest.parent / f"{dest.name}.sha256").write_text(checksum)
            _log(f"  ✓ {name}/{path} sha256={checksum[:12]}")
            for w in warnings:
                _log(f"  ℹ {w}")
            result["synced"].append({"repo": name, "path": path, "checksum": checksum})

    _log(
        f"sync_all done — {len(result['synced'])} synced, "
        f"{len(result['skipped'])} skipped, {len(result['warnings'])} warnings"
    )
    return result
