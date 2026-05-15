"""Playbook corpus loader and search.

A mentor's corpus is a two-level hierarchy:

* **Playbook** — an *area of practice*. One subdirectory under
  ``<mentor_dir>/playbooks/<slug>/``, with a ``playbook.json``
  carrying the playbook's name + description.
* **Skill** — one fine-grained craft rule, one Markdown file with
  YAML frontmatter (the [AgentSkills](https://agentskills.io) ``SKILL.md``
  format) or one legacy ``*.md`` work-instruction file. The skill's
  ``id`` is its parent directory's name (AgentSkills) or its
  filename stem (legacy).

This split tracks the conceptual model: an AMMP playbook describes
*what a mentor knows how to do* (an area of practice), and a skill
is *one specific rule inside that area*. ``AskMentor`` prompts get
the whole corpus flattened across playbooks.

Two storage layouts are supported, decided per-playbook by its
``playbook.json``:

* **Local** — the playbook directory holds either ``skills/<id>/SKILL.md``
  folders (AgentSkills format) or ``NN-*.md`` files (legacy format).
* **Plugin-backed** — ``playbook.json`` carries a ``plugin`` field
  with a ``<plugin-name>@<marketplace-name>`` reference. The loader
  resolves that to
  ``<marketplaces_root>/<marketplace>/plugins/<plugin>/skills/<id>/SKILL.md``
  and pulls the skill content from there. The marketplace must be
  git-cloned into ``<marketplaces_root>`` ahead of time.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Skill:
    """One skill loaded from disk.

    Attributes:
        id: Slug used by the ``GetSkill`` wire op. SKILL.md folder
            name for AgentSkills format; filename stem for legacy format.
        title: First H1 in the body (or the description, or the id).
        summary: One-line ``description`` from frontmatter, or the
            first non-blank non-heading body line as a fallback.
        body: Full Markdown source as the mentee sees it.
        path: Filesystem path to the loaded file.
        playbook_id: Slug of the playbook this skill belongs to.
        order: Optional ordering hint from ``metadata.order`` in
            frontmatter (AgentSkills) or the ``NN-`` filename prefix
            (legacy). Used to sort within a playbook.
    """

    id: str
    title: str
    summary: str
    body: str
    path: Path
    playbook_id: str
    order: int = 0


@dataclass(frozen=True, slots=True)
class Playbook:
    """One playbook — an area of practice. Loaded from a subdirectory.

    Attributes:
        id: Directory name (slug) — the on-the-wire id used by
            ``GetPlaybook``.
        name: Human-readable display name from ``playbook.json``.
        description: One-line mentee-facing summary from
            ``playbook.json``.
        dir: Filesystem path to the playbook directory.
        plugin_ref: When the playbook is plugin-backed,
            ``(plugin_name, marketplace_name)``; ``None`` when the
            playbook holds its skills locally.
        commercial: ``True`` when the backing plugin's
            ``plugin.json`` declares ``"commercial": true`` (private
            helmguild-plugins marketplace). ``False`` for community-
            licensed plugins or when no plugin backs the playbook.
        requires: List of playbook ids this playbook depends on
            (other playbooks under the same mentor). Read from the
            ``"requires"`` array in ``playbook.json``; empty list
            when absent. Mentee agents are expected to load the
            required playbooks before the dependent one.
        validation: Acceptance-test specification for proving a
            mentee actually learned the playbook. Read from the
            ``"validation"`` object in ``playbook.json``. Shape:
            ``{"prompts": [{"id", "task", "expect": {...}}, ...]}``.
            Empty list of prompts when absent. A separate validator
            (``ammp playbook validate <id>``) spawns a fresh mentee,
            issues each prompt, and runs the expect-rules against
            the response.
        skills: Skills loaded from this playbook, sorted by
            ``order`` then ``id``.
    """

    id: str
    name: str
    description: str
    dir: Path
    plugin_ref: tuple[str, str] | None = None
    commercial: bool = False
    requires: list[str] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    skills: list[Skill] = field(default_factory=list)


# `[^\n]+` instead of `.+` so the title group never backtracks across
# lines (avoids the catastrophic-backtracking class S5852 flags). Same
# match semantics with re.MULTILINE — the title is whatever follows
# `# ` on a single line.
_TITLE_RE = re.compile(r"^#\s+([^\n]+)$", re.MULTILINE)
# Bounded quantifier (`{0,32768}?`) avoids the unbounded backtracking
# class S5852 still flags. Frontmatter on a real instruction is well
# under 32 KB; capping the quantifier keeps the pattern matching the
# same set of inputs we care about while making catastrophic
# backtracking impossible by construction.
_FRONTMATTER_RE = re.compile(r"^---\n[\s\S]{0,32768}?\n---\n")

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_PLUGIN_REF_RE = re.compile(r"^([a-z][a-z0-9-]*)@([a-z][a-z0-9-]*)$")
_LEGACY_PREFIX_RE = re.compile(r"^(\d+)-")


def _summarise(body: str) -> str:
    """Return the first non-blank, non-heading line, truncated to ~200 chars."""
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:200] + ("…" if len(s) > 200 else "")
    return ""


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Pull frontmatter out of a SKILL.md / legacy ``*.md`` body.

    Lazy single-pass parse using PyYAML if available; otherwise a
    tiny hand-roll covering the keys we read (``description``, plus
    ``metadata.order``). The hand-roll is enough because we only need
    a handful of fields and the YAML in our corpus is flat.

    Args:
        text: Full file content (frontmatter + body, or just body).

    Returns:
        Parsed frontmatter dict; empty dict when no frontmatter
        delimiter is present.
    """
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    raw = text[4:end]
    try:
        import yaml

        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except ImportError:
        # Fallback: parse the small set of keys we actually use.
        # Handles `key: value` and one-level `metadata:` sub-block.
        out: dict[str, Any] = {}
        current_block: dict[str, Any] | None = None
        for line in raw.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("  ") and current_block is not None:
                k, _, v = line.strip().partition(":")
                if v:
                    current_block[k.strip()] = v.strip().strip('"').strip("'")
            else:
                k, _, v = line.partition(":")
                if not v.strip():
                    current_block = {}
                    out[k.strip()] = current_block
                else:
                    out[k.strip()] = v.strip().strip('"').strip("'")
                    current_block = None
        return out


def _strip_frontmatter(text: str) -> str:
    """Return the body of a Markdown file with the leading YAML frontmatter removed."""
    return _FRONTMATTER_RE.sub("", text, count=1)


def _load_skill_md(skill_dir: Path, playbook_id: str) -> Skill:
    """Read one AgentSkills ``SKILL.md`` folder into a :class:`Skill`.

    The skill's ``id`` is the folder name. ``description`` for the
    summary comes from frontmatter; ``title`` from the body's first
    H1 (or falls back to the description / id).

    Args:
        skill_dir: ``<…>/skills/<skill-id>/`` folder.
        playbook_id: Slug of the playbook this skill belongs to.

    Returns:
        The fully-loaded :class:`Skill`.
    """
    md_path = skill_dir / "SKILL.md"
    text = md_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    body_after_fm = _strip_frontmatter(text)
    title_match = _TITLE_RE.search(body_after_fm)
    description = str(fm.get("description") or "")
    title = title_match.group(1).strip() if title_match else (description or skill_dir.name)
    body_after_title = body_after_fm[title_match.end() :] if title_match else body_after_fm
    summary = description or _summarise(body_after_title)
    order_value = 0
    if isinstance(fm.get("metadata"), dict):
        raw_order = fm["metadata"].get("order")
        if isinstance(raw_order, int):
            order_value = raw_order
        elif isinstance(raw_order, str) and raw_order.isdigit():
            order_value = int(raw_order)
    return Skill(
        id=skill_dir.name,
        title=title,
        summary=summary,
        body=text,
        path=md_path,
        playbook_id=playbook_id,
        order=order_value,
    )


def _load_legacy_md(path: Path, playbook_id: str) -> Skill:
    """Read one legacy ``NN-*.md`` skill file into a :class:`Skill`.

    Preserves the original AMMP-01 layout: filename stem is the id,
    ``NN-`` prefix sets the order, the first H1 is the title, the
    first non-blank non-heading line is the summary.

    Args:
        path: Filesystem path to a single ``*.md`` skill file.
        playbook_id: Slug of the playbook this skill belongs to.

    Returns:
        The fully-loaded :class:`Skill` with ``id`` = filename stem.
    """
    text = path.read_text(encoding="utf-8")
    body_after_fm = _strip_frontmatter(text)
    title_match = _TITLE_RE.search(body_after_fm)
    title = title_match.group(1).strip() if title_match else path.stem
    body_after_title = body_after_fm[title_match.end() :] if title_match else body_after_fm
    summary = _summarise(body_after_title)
    order_match = _LEGACY_PREFIX_RE.match(path.stem)
    order_value = int(order_match.group(1)) if order_match else 0
    return Skill(
        id=path.stem,
        title=title,
        summary=summary,
        body=text,
        path=path,
        playbook_id=playbook_id,
        order=order_value,
    )


def _parse_plugin_ref(raw: object) -> tuple[str, str] | None:
    """Parse a ``<plugin>@<marketplace>`` reference string.

    Args:
        raw: Value of the ``plugin`` key from ``playbook.json``.

    Returns:
        ``(plugin_name, marketplace_name)`` on success, ``None`` when
        the value is missing or malformed (the caller falls back to
        local-skills loading and logs a warning).
    """
    if not isinstance(raw, str):
        return None
    match = _PLUGIN_REF_RE.match(raw.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _load_skills_from_plugin(plugin_dir: Path, playbook_id: str) -> list[Skill]:
    """Walk a marketplace plugin's ``skills/`` folder and load every SKILL.md.

    Args:
        plugin_dir: Resolved plugin directory under the marketplace
            clone (``<marketplaces_root>/<marketplace>/plugins/<plugin>/``).
        playbook_id: Slug of the playbook this plugin is bound to.

    Returns:
        Skills sorted by ``order`` then ``id``.
    """
    skills_root = plugin_dir / "skills"
    if not skills_root.is_dir():
        logger.warning("plugin %s has no skills/ subdirectory", plugin_dir)
        return []
    skills: list[Skill] = []
    for sk_dir in sorted(skills_root.iterdir()):
        if not sk_dir.is_dir() or not (sk_dir / "SKILL.md").is_file():
            continue
        skills.append(_load_skill_md(sk_dir, playbook_id))
    skills.sort(key=lambda s: (s.order, s.id))
    return skills


def _load_skills_local(playbook_dir: Path, playbook_id: str) -> list[Skill]:
    """Load skills from a playbook directory that holds them locally.

    Supports both layouts:

    * ``<playbook_dir>/skills/<id>/SKILL.md`` (AgentSkills format)
    * ``<playbook_dir>/NN-*.md`` (legacy AMMP-01 layout)

    The AgentSkills folder, when present, wins (we prefer the modern
    layout). Falls through to legacy ``*.md`` only when the ``skills/``
    folder is absent or empty.

    Args:
        playbook_dir: Playbook directory containing ``playbook.json``.
        playbook_id: Slug of the playbook (== ``playbook_dir.name``).

    Returns:
        Skills sorted by ``order`` then ``id``.
    """
    skills_root = playbook_dir / "skills"
    if skills_root.is_dir():
        skills = [
            _load_skill_md(sk, playbook_id)
            for sk in sorted(skills_root.iterdir())
            if sk.is_dir() and (sk / "SKILL.md").is_file()
        ]
        if skills:
            skills.sort(key=lambda s: (s.order, s.id))
            return skills
    # Legacy AMMP-01: NN-*.md files under the playbook dir
    skills = [
        _load_legacy_md(p, playbook_id) for p in sorted(playbook_dir.glob("*.md")) if p.name.lower() != "readme.md"
    ]
    skills.sort(key=lambda s: (s.order, s.id))
    return skills


def _load_one_playbook(playbook_dir: Path, marketplaces_root: Path | None = None) -> Playbook | None:
    """Load one playbook directory into a :class:`Playbook`.

    Resolution order:

    1. ``playbook.json`` carries a ``plugin: "<name>@<marketplace>"``
       reference → load skills from the marketplace clone.
    2. ``playbook.json`` exists without a plugin ref → load skills
       locally from the playbook directory (AgentSkills folder
       layout or legacy ``*.md`` files).

    Args:
        playbook_dir: Directory containing ``playbook.json``.
        marketplaces_root: Root of the marketplace clones. Required
            only when a playbook references a plugin; ``None`` is fine
            for purely-local mentor configurations.

    Returns:
        The loaded :class:`Playbook`, or ``None`` if the directory
        has no ``playbook.json``.
    """
    meta_file = playbook_dir / "playbook.json"
    if not meta_file.is_file():
        return None
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    name = str(meta.get("name") or playbook_dir.name)
    description = str(meta.get("description") or "")
    pid = playbook_dir.name

    plugin_ref = _parse_plugin_ref(meta.get("plugin"))
    commercial = False
    if plugin_ref:
        plugin_name, marketplace_name = plugin_ref
        if marketplaces_root is None:
            logger.warning(
                "playbook %s references plugin %s@%s but marketplaces_root is unset; falling back to local skills",
                pid,
                plugin_name,
                marketplace_name,
            )
            skills = _load_skills_local(playbook_dir, pid)
        else:
            plugin_dir = marketplaces_root / marketplace_name / "plugins" / plugin_name
            if not plugin_dir.is_dir():
                logger.warning(
                    "playbook %s references plugin %s@%s but %s does not exist; falling back to local skills",
                    pid,
                    plugin_name,
                    marketplace_name,
                    plugin_dir,
                )
                skills = _load_skills_local(playbook_dir, pid)
            else:
                skills = _load_skills_from_plugin(plugin_dir, pid)
                # Surface the plugin's commercial flag from the
                # marketplace catalogue. Claude Code's plugin.json
                # schema rejects custom top-level keys, so the
                # commercial flag lives only on `marketplace.json` —
                # both at `metadata.commercial` and per-entry. We
                # prefer the per-entry value when present; otherwise
                # inherit from `metadata.commercial`.
                mp_manifest = marketplaces_root / marketplace_name / ".claude-plugin" / "marketplace.json"
                if mp_manifest.is_file():
                    try:
                        mp = json.loads(mp_manifest.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        mp = {}
                    entry = next(
                        (p for p in mp.get("plugins") or [] if isinstance(p, dict) and p.get("name") == plugin_name),
                        None,
                    )
                    if entry is not None and "commercial" in entry:
                        commercial = bool(entry["commercial"])
                    else:
                        commercial = bool((mp.get("metadata") or {}).get("commercial"))
    else:
        skills = _load_skills_local(playbook_dir, pid)

    # `requires` — list of sibling playbook ids this playbook depends
    # on. Optional; absent or non-list → empty list.
    requires_raw = meta.get("requires")
    requires: list[str] = []
    if isinstance(requires_raw, list):
        requires = [str(r) for r in requires_raw if isinstance(r, str) and r]

    # `validation` — acceptance-test spec for proving a mentee learned
    # the playbook. Optional; absent or malformed → empty dict.
    validation_raw = meta.get("validation")
    validation: dict[str, Any] = {}
    if isinstance(validation_raw, dict):
        prompts_raw = validation_raw.get("prompts")
        if isinstance(prompts_raw, list):
            kept: list[dict[str, Any]] = []
            for p in prompts_raw:
                if not isinstance(p, dict):
                    continue
                pid_v = p.get("id")
                task = p.get("task")
                if isinstance(pid_v, str) and isinstance(task, str) and pid_v and task:
                    expect = p.get("expect")
                    mode = p.get("mode")
                    prompt_entry: dict[str, Any] = {
                        "id": pid_v,
                        "task": task,
                        "expect": expect if isinstance(expect, dict) else {},
                    }
                    # `mode` is optional; default is "comprehension" (Layer 1).
                    # "behavioral" routes to the Layer 2 harness with plugin
                    # install + filesystem side-effect assertions.
                    if isinstance(mode, str) and mode in ("comprehension", "behavioral"):
                        prompt_entry["mode"] = mode
                    kept.append(prompt_entry)
            validation = {"prompts": kept}
        # `goal` is the optional Layer-3 acceptance-test object —
        # one composite scenario per playbook, distinct from the
        # `prompts[]` array. Carries `id`, `task`, `expect` (with
        # extended rules: must_contain_in_file, consumer_check).
        # Pass it through verbatim if shaped right; loader doesn't
        # re-validate every nested key (the harness defends).
        goal_raw = validation_raw.get("goal")
        if isinstance(goal_raw, dict) and isinstance(goal_raw.get("task"), str) and goal_raw["task"]:
            validation["goal"] = goal_raw

    return Playbook(
        id=pid,
        name=name,
        description=description,
        dir=playbook_dir,
        plugin_ref=plugin_ref,
        commercial=commercial,
        requires=requires,
        validation=validation,
        skills=skills,
    )


def load_playbooks(playbook_root: Path, marketplaces_root: Path | None = None) -> list[Playbook]:
    """Scan a mentor's ``playbooks/`` directory and load each playbook.

    Each subdirectory containing a ``playbook.json`` is a playbook;
    its skills load either from a referenced marketplace plugin or
    from local files inside the playbook directory.

    Args:
        playbook_root: Mentor's ``playbooks/`` directory.
        marketplaces_root: Root of marketplace clones (e.g.
            ``~/.ammp/marketplaces/``). Required only when a playbook
            references a plugin via ``playbook.json``.

    Returns:
        Playbooks sorted by directory name. Empty list when
        ``playbook_root`` does not exist.
    """
    if not playbook_root.is_dir():
        return []
    out: list[Playbook] = []
    for sub in sorted(p for p in playbook_root.iterdir() if p.is_dir()):
        if not _SLUG_RE.match(sub.name):
            continue
        pb = _load_one_playbook(sub, marketplaces_root=marketplaces_root)
        if pb is None:
            logger.warning("skipping %s — no playbook.json", sub)
            continue
        out.append(pb)
    return out


def flatten_skills(corpus: list[Playbook]) -> list[Skill]:
    """Flatten a playbook corpus into a single list of skills.

    Used by ``AskMentor`` to feed every skill across every playbook
    into the keyword ranker, and by the audit/search paths that want
    to operate on skill granularity.

    Args:
        corpus: Playbooks to flatten.

    Returns:
        Every :class:`Skill` from every playbook, in playbook order
        then file order.
    """
    return [sk for pb in corpus for sk in pb.skills]


def safe_id(raw: str) -> str | None:
    """Validate a playbook or skill id from untrusted mentee input.

    Args:
        raw: Caller-supplied id string.

    Returns:
        Canonical slug, or ``None`` when rejected (path-traversal
        sequences, dotfile prefix, empty, longer than 128 chars).
    """
    s = raw.strip()
    if not s or "/" in s or "\\" in s or s.startswith(".") or len(s) > 128:
        return None
    return s


def search(corpus: list[Playbook], query: str, limit: int = 5) -> list[tuple[Skill, int, str]]:
    """Substring-rank a corpus for a query string, returning skills.

    Searches across every skill in every playbook. Results carry
    their ``playbook_id`` via the ``Skill`` itself, so callers can
    render hits in their playbook context without an extra lookup.

    Args:
        corpus: Playbooks to search.
        query: User-supplied query string. Empty / whitespace returns
            an empty list.
        limit: Maximum number of matches to return.

    Returns:
        ``(skill, rank, snippet)`` triples sorted by descending rank.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    rows: list[tuple[Skill, int, str]] = []
    for sk in flatten_skills(corpus):
        body_lower = sk.body.lower()
        idx = body_lower.find(q)
        if idx < 0:
            continue
        rank = body_lower.count(q)
        start = max(0, idx - 80)
        end = min(len(sk.body), idx + len(q) + 80)
        snippet = sk.body[start:end].replace("\n", " ").strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(sk.body):
            snippet = snippet + "…"
        rows.append((sk, rank, snippet))
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]


def keyword_rank(corpus: list[Playbook], question: str, limit: int = 3) -> list[tuple[Skill, int]]:
    """Token-rank a corpus against a question for cheap retrieval.

    Used by ``AskMentor`` to feed the top-N most relevant skills
    into the backend's prompt. Stopwords are filtered.

    Args:
        corpus: Playbooks to score.
        question: The mentee's question.
        limit: Maximum number of matches to return.

    Returns:
        ``(skill, rank)`` pairs sorted by descending rank.
    """
    stopwords = {
        "the", "and", "for", "with", "what", "when", "how", "why", "you",
        "are", "this", "that", "from", "into", "over", "about", "your",
        "have", "has", "had", "was", "were", "will", "can", "could",
        "should", "would", "but", "not", "any", "all", "out", "off",
    }  # fmt: skip
    tokens = [t for t in re.findall(r"[a-z][a-z0-9-]+", question.lower()) if len(t) >= 3 and t not in stopwords]
    if not tokens:
        return []
    rows: list[tuple[Skill, int]] = []
    for sk in flatten_skills(corpus):
        body_lower = sk.body.lower()
        rank = sum(body_lower.count(t) for t in tokens)
        if rank > 0:
            rows.append((sk, rank))
    rows.sort(key=lambda r: -r[1])
    return rows[:limit]


# ─── Plugin archive — shared logic for `GetPluginArchive` MCP tool + `ammp plugin archive` CLI ────


_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def enumerate_plugin_refs(
    mentors: Mapping[str, object],
    marketplaces_root: Path | None,
) -> dict[str, tuple[str, str, Path]]:
    """Enumerate plugin references resolving to a marketplace clone on disk.

    Walks every mentor's playbook corpus, collects each
    ``(plugin, marketplace)`` ref from the ``playbook.json`` files,
    and keeps only those whose marketplace clone is present at
    ``marketplaces_root/<marketplace>/plugins/<plugin>/``.

    Keyed by plugin name (one plugin name lives in exactly one marketplace,
    by construction). Unresolvable references (missing clone) are
    silently dropped so a stale config can't be probed via the wire.

    Args:
        mentors: Mapping of mentor slug → :class:`Mentor` (or any object
            exposing ``playbook_dir: Path``). Decoupled from the
            ``Mentor`` class to keep this helper testable without a
            full server context.
        marketplaces_root: Root directory holding marketplace clones,
            or ``None`` when the operator hasn't configured one.

    Returns:
        ``{plugin_name: (plugin_name, marketplace_name, plugin_dir)}``.
        Empty when no playbook references a plugin or none resolve.
    """
    if marketplaces_root is None:
        return {}
    out: dict[str, tuple[str, str, Path]] = {}
    for mentor in mentors.values():
        playbook_dir = getattr(mentor, "playbook_dir", None)
        if playbook_dir is None:
            continue
        for pb in load_playbooks(playbook_dir, marketplaces_root=marketplaces_root):
            if pb.plugin_ref is None:
                continue
            plugin, marketplace = pb.plugin_ref
            plugin_dir = marketplaces_root / marketplace / "plugins" / plugin
            if plugin_dir.is_dir():
                out[plugin] = (plugin, marketplace, plugin_dir)
    return out


def build_plugin_archive_response(
    public_url: str,
    plugin: str,
    known_refs: dict[str, tuple[str, str, Path]],
) -> dict[str, str]:
    """Render the response envelope for a plugin-archive lookup.

    Pure function — no auth, no audit, no I/O. Both the MCP handler
    (``_handle_get_plugin_archive``) and the CLI (``ammp plugin archive``)
    call this with the same enumerated refs so the two surfaces stay
    in lockstep.

    Args:
        public_url: Server's advertised URL (no trailing slash).
        plugin: Plugin name requested by the caller (kebab-case slug).
        known_refs: Output of :func:`enumerate_plugin_refs` for this
            server's mentors + marketplaces_root.

    Returns:
        On success: ``{plugin, marketplace, archive_url, install_instructions}``.
        On failure: ``{error: "invalid_plugin" | "not_found"}``.
    """
    if not _PLUGIN_NAME_RE.match(plugin):
        return {"error": "invalid_plugin"}
    entry = known_refs.get(plugin)
    if entry is None:
        return {"error": "not_found"}
    _, marketplace, _ = entry
    base = public_url.rstrip("/")
    archive_url = f"{base}/plugins/{plugin}.zip"
    install_instructions = (
        "Download the zip from `archive_url` (the same Bearer token authenticates "
        "the download). Then ask your user to install the plugin into their Claude "
        "Code / Desktop runtime by extracting the zip and running "
        "`/plugin install <path-to-extracted-folder>`, or by uploading the zip via "
        "the runtime's plugin installer. After install, the plugin's `.mcp.json` "
        "wires this same AMMP server, so the live ops continue to work."
    )
    return {
        "plugin": plugin,
        "marketplace": marketplace,
        "archive_url": archive_url,
        "install_instructions": install_instructions,
    }
