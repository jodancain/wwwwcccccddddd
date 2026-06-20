"""Skill manager: load, save, list, delete persona skills.

Skills are stored as SKILL.md files in data/skills/{slug}/ directories,
following the AgentSkills open standard format.
"""
import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config.settings import get_settings


SKILLS_DIR = Path(get_settings().DATA_DIR) / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def list_skills() -> list[dict]:
    """List all available skills with metadata."""
    skills = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        meta = _parse_skill_frontmatter(skill_file.read_text(encoding="utf-8", errors="ignore"))
        meta["slug"] = skill_dir.name
        meta["path"] = str(skill_file)
        meta["size"] = skill_file.stat().st_size
        skills.append(meta)
    return skills


def get_skill(slug: str) -> Optional[dict]:
    """Load a skill by slug, returning metadata + full content."""
    slug = _sanitize_slug(slug)
    skill_dir = _skill_dir(slug)
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None
    content = skill_file.read_text(encoding="utf-8", errors="ignore")
    meta = _parse_skill_frontmatter(content)
    meta["slug"] = slug
    meta["content"] = content
    # Extract the body (after frontmatter)
    meta["body"] = _extract_body(content)
    return meta


def save_skill(slug: str, content: str) -> dict:
    """Save a skill. Content should be full SKILL.md with frontmatter."""
    slug = _sanitize_slug(slug)
    skill_dir = _skill_dir(slug)
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    logger.info(f"Skill saved: {slug}")
    meta = _parse_skill_frontmatter(content)
    meta["slug"] = slug
    meta["path"] = str(skill_file)
    return meta


def delete_skill(slug: str) -> bool:
    """Delete a skill directory."""
    slug = _sanitize_slug(slug)
    skill_dir = _skill_dir(slug)
    if not skill_dir.exists():
        return False
    import shutil
    shutil.rmtree(skill_dir)
    logger.info(f"Skill deleted: {slug}")
    return True


def import_skill_from_url(url: str) -> dict:
    """Import a skill from a GitHub URL (raw SKILL.md or repo)."""
    import httpx

    # Convert GitHub repo URL to raw SKILL.md URL
    if "github.com" in url and "/blob/" not in url and "raw.githubusercontent.com" not in url:
        # Repo URL like https://github.com/user/repo
        raw_url = url.rstrip("/").replace("github.com", "raw.githubusercontent.com") + "/main/SKILL.md"
    elif "/blob/" in url:
        raw_url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    else:
        raw_url = url

    resp = httpx.get(raw_url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    content = resp.text

    # Extract slug from frontmatter name field
    meta = _parse_skill_frontmatter(content)
    slug = _sanitize_slug(meta.get("name", f"imported-{int(time.time())}"))

    return save_skill(slug, content)


def build_skill_system_prompt(slug: str) -> str:
    """Build a system prompt that incorporates the skill's persona."""
    skill = get_skill(slug)
    if not skill:
        return ""
    body = skill.get("body", "")
    name = skill.get("name", slug)

    return f"""你现在正在使用人物 Skill：「{name}」

以下是该人物的完整定义：

{body}

---

请注意：
- 当用户问关于这个人物的问题时，用这个人物的视角和风格回答
- 如果用户在分析微信对话，结合人物的性格特点给出分析
- 保持人物的说话风格和用词习惯
"""


def _parse_skill_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from SKILL.md."""
    meta = {}
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return meta
    frontmatter = match.group(1)
    for line in frontmatter.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value:
                meta[key] = value
    return meta


def _extract_body(content: str) -> str:
    """Extract body content after frontmatter."""
    match = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
    if match:
        return content[match.end():]
    return content


def _sanitize_slug(name: str) -> str:
    """Convert name to URL-safe slug."""
    slug = re.sub(r"[^\w\u4e00-\u9fff-]", "-", name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "unnamed-skill"


def _skill_dir(slug: str) -> Path:
    """Return a skill directory path guaranteed to stay under SKILLS_DIR."""
    skill_dir = (SKILLS_DIR / slug).resolve()
    skills_root = SKILLS_DIR.resolve()
    if skill_dir != skills_root and skills_root not in skill_dir.parents:
        raise ValueError("Invalid skill slug")
    return skill_dir
