import glob
import os

import yaml


def _meta_get(meta: dict, key: str, default=None):
    """Case-insensitive frontmatter lookup so both `Name:`/`Description:` and the
    lowercase `name:`/`description:` (Claude-Code style) conventions work."""
    if key in meta:
        return meta[key]
    low = key.lower()
    for k, v in meta.items():
        if isinstance(k, str) and k.lower() == low:
            return v
    return default


def _parse_frontmatter(text: str):
    """Return (meta_dict, body_str) from a markdown file with YAML frontmatter."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except Exception:
                meta = {}
            return meta, parts[2].lstrip("\n")
    return {}, text


def _main_file(folder: str, key: str):
    """The primary markdown file for an action folder: {key}.md, else first *.md."""
    cand = os.path.join(folder, f"{key}.md")
    if os.path.exists(cand):
        return cand
    mds = sorted(glob.glob(os.path.join(folder, "*.md")))
    return mds[0] if mds else None


def scan_actions(actions_dir: str):
    """Scan the actions directory; one folder per action. Returns metadata list."""
    out = []
    if not os.path.isdir(actions_dir):
        return out
    for key in sorted(os.listdir(actions_dir)):
        folder = os.path.join(actions_dir, key)
        if not os.path.isdir(folder):
            continue
        mf = _main_file(folder, key)
        if not mf:
            continue
        try:
            with open(mf, errors="replace") as f:
                meta, _ = _parse_frontmatter(f.read())
        except Exception:
            meta = {}
        out.append({
            "key": key,
            "name": _meta_get(meta, "Name", key),
            "description": (_meta_get(meta, "Description") or "").strip(),
            "folder": folder,
            "file": mf,
            "meta": meta,
        })
    return out


def build_index(actions) -> str:
    """The concatenated name/description list injected into the system prompt."""
    if not actions:
        return "(No action files are currently available.)"
    return "\n".join(f"- {a['key']}: {a['description']}".rstrip() for a in actions)


def load_action(actions_dir: str, key: str, visited=None) -> str:
    """Load an action's body + its supplementary file paths, recursively following
    AlsoLoad. `visited` accumulates every key loaded (for reporting)."""
    if visited is None:
        visited = set()
    if key in visited:
        return ""
    visited.add(key)

    folder = os.path.join(actions_dir, key)
    mf = _main_file(folder, key)
    if not mf or not os.path.isdir(folder):
        return f"[Action '{key}' not found]"

    with open(mf, errors="replace") as f:
        meta, body = _parse_frontmatter(f.read())

    # Resolve $SKILL_DIR / ${SKILL_DIR} references to the action's absolute folder
    # so scripts and assets shipped with the action can be invoked by path.
    skill_dir = os.path.abspath(folder)
    body = body.replace("${SKILL_DIR}", skill_dir).replace("$SKILL_DIR", skill_dir)

    supp = [
        os.path.join(folder, x)
        for x in sorted(os.listdir(folder))
        if os.path.join(folder, x) != mf and os.path.isfile(os.path.join(folder, x))
    ]

    block = [f"===== ACTION: {_meta_get(meta, 'Name', key)} =====", body.strip()]
    if supp:
        block.append("Supplementary files (read with the read tool if needed):")
        block.extend(f"  - {p}" for p in supp)
    result = "\n".join(block)

    also = _meta_get(meta, "AlsoLoad") or []
    if isinstance(also, str):
        also = [also]
    dep_blocks = []
    for dep in also:
        sub = load_action(actions_dir, str(dep).strip(), visited)
        if sub:
            dep_blocks.append(sub)
    if dep_blocks:
        result += "\n\n" + "\n\n".join(dep_blocks)
    return result
