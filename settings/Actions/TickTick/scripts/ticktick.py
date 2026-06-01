#!/usr/bin/env python3
"""
TickTick CLI helper for 3Jane.

Usage:
  python ticktick.py search "query" [--on DATE] [--limit N]
  python ticktick.py add "task title" [--description "..."] [--due DATE] \
      [--project "Name"] [--labels "tag1,tag2"] [--priority 0|1|3|5] [--repeat "RRULE:..."]

Outputs JSON to stdout. Errors/warnings go to stderr.

Auth: reads TICKTICK_USERNAME / TICKTICK_PASSWORD from the environment (optionally
TICKTICK_TOTP_SECRET for 2FA). It talks to TickTick's v2 API via the `pyticktick`
library, which sees ALL tasks including the Inbox. The credentials are read from the
environment only — never passed as arguments or printed — so they never appear in the
bot's thinking log. The v2 session token is cached under /workspace so we don't log in
on every run.
"""

import sys
import os
import re
import json
import datetime
import argparse
import warnings

# pyticktick logs via loguru and pydantic may emit warnings; silence both so nothing
# (least of all anything credential-adjacent) leaks into captured output.
warnings.filterwarnings("ignore")
try:
    from loguru import logger as _loguru_logger
    _loguru_logger.remove()
except Exception:
    pass

from thefuzz import fuzz, process
from bson import ObjectId
from pyticktick import Client
from pyticktick.models.v2 import CreateTaskV2, PostBatchTaskV2

CACHE_PATH = os.environ.get("TICKTICK_TOKEN_CACHE", "/workspace/.cache/ticktick.json")


# ---------------------------------------------------------------------------
# Auth + token caching
# ---------------------------------------------------------------------------

def _get_creds():
    user = os.environ.get("TICKTICK_USERNAME")
    pw = os.environ.get("TICKTICK_PASSWORD")
    if not user or not pw:
        print("Error: TICKTICK_USERNAME and TICKTICK_PASSWORD must be set.", file=sys.stderr)
        sys.exit(1)
    return user, pw, (os.environ.get("TICKTICK_TOTP_SECRET") or None)


def _load_token():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f).get("v2_token")
    except Exception:
        return None


def _save_token(token):
    if not token:
        return
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump({"v2_token": token}, f)
        os.chmod(CACHE_PATH, 0o600)
    except Exception:
        pass


def _make_client(creds, token):
    """Build a client. With a token (and creds present) pyticktick skips the network
    sign-on; without one it logs in at construction."""
    user, pw, totp = creds
    kwargs = {"v2_username": user, "v2_password": pw, "override_forbid_extra": True}
    if totp:
        kwargs["v2_totp_secret"] = totp
    if token:
        kwargs["v2_token"] = token
    return Client(**kwargs)


def with_client(fn):
    """Run fn(client). Tries the cached session token first; if it's stale (any error),
    logs in fresh once, caches the new token, and runs fn. A fresh-login attempt is not
    retried, so a write is never issued twice."""
    creds = _get_creds()
    token = _load_token()
    if token:
        try:
            return fn(_make_client(creds, token))
        except SystemExit:
            raise
        except Exception:
            pass  # cached token likely expired — fall through to a fresh login
    client = _make_client(creds, None)
    _save_token(client.v2_token)
    return fn(client)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_REL_DAYS = {'today': 0, 'tomorrow': 1, 'tomorow': 1, 'yesterday': -1}


def resolve_date(s: str) -> str:
    """Resolve a date expression to an ISO date string (YYYY-MM-DD), computed from the
    local clock. Accepts an ISO date, today/tomorrow/yesterday, or a signed day offset
    like '+1' / '-2'. Date math is done here so relative days are always correct."""
    s = s.strip().lower()
    if _ISO_DATE_RE.match(s):
        return s
    today = datetime.date.today()
    if s in _REL_DAYS:
        return str(today + datetime.timedelta(days=_REL_DAYS[s]))
    m = re.match(r'^([+-]?\d+)d?$', s)
    if m:
        return str(today + datetime.timedelta(days=int(m.group(1))))
    raise ValueError(f"Unrecognized date expression: {s!r}")


def _due_str(task) -> str:
    """The YYYY-MM-DD portion of a task's due date, or '' if it has none."""
    return task.due_date.date().isoformat() if task.due_date else ""


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _task_url(project_id, task_id) -> str:
    if project_id and task_id:
        return f"https://ticktick.com/webapp/#p/{project_id}/tasks/{task_id}"
    return "https://ticktick.com/webapp/"


def _fmt_task(task, proj_name, score):
    """Shape a TaskV2 into the output schema (matches the Todoist skill's fields)."""
    return {
        'id': task.id,
        'content': task.title,
        'description': (task.content or task.desc or None),
        'project': proj_name.get(task.project_id, ''),
        'due': _due_str(task) or None,
        'labels': list(task.tags or []),
        'priority': task.priority,
        'url': _task_url(task.project_id, task.id),
        'match_score': score,
    }


def _project_names(batch):
    names = {p.id: p.name for p in batch.project_profiles}
    names[batch.inbox_id] = "Inbox"
    return names


def _active_tasks(batch):
    # status 0 == active/uncompleted (1/2 completed, -1 abandoned).
    return [t for t in batch.sync_task_bean.update if t.status == 0]


def _clean_tags(raw):
    """TickTick tag names must be lowercase with no spaces or special characters.
    Lowercase each tag and drop any that still can't be a valid tag (warning instead
    of failing the whole add)."""
    if not raw:
        return []
    out = []
    for t in raw.split(','):
        t = t.strip().lower()
        if not t:
            continue
        if re.search(r'[\\/"#:*?<>|\s]', t):
            print(f"Warning: skipping invalid tag {t!r} (tags must be a single lowercase word).",
                  file=sys.stderr)
            continue
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(args):
    query = (args.query or '').lower()
    limit = args.limit
    if not query and not args.on:
        print("Error: provide a search query or --on DATE.", file=sys.stderr)
        sys.exit(1)

    # Resolve an exact target date here (so "tomorrow" is always right) before any
    # network call. Fuzzy scoring treats adjacent dates as near matches, so date
    # lookups must be exact, not fuzzy.
    target_date = None
    if args.on:
        target_date = resolve_date(args.on)
    elif _ISO_DATE_RE.match(query):
        target_date = query

    def run(client):
        batch = client.get_batch_v2()
        proj_name = _project_names(batch)
        tasks = _active_tasks(batch)

        if target_date:
            out = [_fmt_task(t, proj_name, 100) for t in tasks if _due_str(t) == target_date]
            return out[:limit]

        results = []
        for task in tasks:
            project_name = proj_name.get(task.project_id, '')
            due = _due_str(task)
            labels_str = ' '.join(task.tags or [])
            title = (task.title or '').lower()
            body = (task.content or task.desc or '').lower()
            # Score fields independently so a due-date search doesn't also require the
            # query to appear in the title.
            scores = [
                fuzz.partial_ratio(query, title),
                fuzz.token_sort_ratio(query, title),
                fuzz.partial_ratio(query, body),
                fuzz.partial_ratio(query, project_name.lower()),
                fuzz.partial_ratio(query, labels_str.lower()),
                fuzz.partial_ratio(query, due),
            ]
            results.append((max(scores), task))
        results.sort(key=lambda x: x[0], reverse=True)
        return [_fmt_task(t, proj_name, score) for score, t in results[:limit]]

    print(json.dumps(with_client(run), indent=2, default=str))


def cmd_add(args):
    # Validate/resolve inputs before authenticating, so a bad date fails fast and
    # cheaply (no pointless login).
    due_dt = None
    if args.due:
        try:
            iso = resolve_date(args.due)
        except ValueError:
            print(f"Error: --due {args.due!r} isn't a concrete date. TickTick's API needs an absolute "
                  f"date (ISO YYYY-MM-DD, today/tomorrow/yesterday, or +N/-N days). For a recurring "
                  f"task use --repeat with an RRULE, e.g. --repeat 'RRULE:FREQ=WEEKLY;BYDAY=MO'.",
                  file=sys.stderr)
            sys.exit(1)
        y, mo, d = (int(x) for x in iso.split('-'))
        due_dt = datetime.datetime(y, mo, d, tzinfo=datetime.timezone.utc)
    tags = _clean_tags(args.labels)

    def run(client):
        batch = client.get_batch_v2()
        name_to_id = {p.name: p.id for p in batch.project_profiles}

        project_id = batch.inbox_id
        project_label = "Inbox"
        if args.project:
            match = (process.extractOne(args.project, list(name_to_id), scorer=fuzz.ratio)
                     if name_to_id else None)
            if match and match[1] >= 55:
                project_id, project_label = name_to_id[match[0]], match[0]
                if match[0].lower() != args.project.lower():
                    print(f"Note: matched project '{args.project}' → '{match[0]}'", file=sys.stderr)
            else:
                print(f"Warning: no project matched '{args.project}' (threshold 55%). Adding to Inbox.",
                      file=sys.stderr)

        task_id = str(ObjectId())  # client-generated id so we can report the URL deterministically
        fields = {"id": task_id, "title": args.content, "project_id": project_id}
        if args.description:
            fields["content"] = args.description
        if due_dt is not None:
            fields["due_date"] = due_dt
            fields["is_all_day"] = True
        if tags:
            fields["tags"] = tags
        if args.priority is not None:
            fields["priority"] = args.priority
        if args.repeat:
            fields["repeat_flag"] = args.repeat

        resp = client.post_task_v2(PostBatchTaskV2(add=[CreateTaskV2(**fields)]))
        if resp.id2error:
            print(f"Warning: TickTick reported errors: {resp.id2error}", file=sys.stderr)
        return {
            'id': task_id,
            'content': args.content,
            'project': project_label,
            'due': (iso if args.due else None),
            'labels': tags,
            'url': _task_url(project_id, task_id),
        }

    result = with_client(run)
    print(json.dumps(result, indent=2, default=str))
    print(f'✓ Added: "{result["content"]}"', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd')

    p_search = sub.add_parser('search')
    p_search.add_argument('query', nargs='?', default=None)
    p_search.add_argument('--on', default=None,
                          help="Exact due date: ISO (YYYY-MM-DD), today/tomorrow/yesterday, or +N/-N days.")
    p_search.add_argument('--limit', type=int, default=10)

    p_add = sub.add_parser('add')
    p_add.add_argument('content')
    p_add.add_argument('--description', default=None)
    p_add.add_argument('--due', default=None,
                       help="Concrete date: ISO, today/tomorrow, or +N/-N. Use --repeat for recurring.")
    p_add.add_argument('--project', default=None)
    p_add.add_argument('--labels', default=None, help='Comma-separated tags (lowercase, single words)')
    p_add.add_argument('--priority', type=int, choices=[0, 1, 3, 5], default=None,
                       help='TickTick priority: 0 none, 1 low, 3 medium, 5 high')
    p_add.add_argument('--repeat', default=None, help="RRULE for recurring tasks, e.g. RRULE:FREQ=WEEKLY;BYDAY=MO")

    args = parser.parse_args()
    if args.cmd == 'search':
        handler = cmd_search
    elif args.cmd == 'add':
        handler = cmd_add
    else:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args)
    except SystemExit:
        raise
    except Exception as e:
        # Surface a clean, single-line error (never a traceback). pyticktick masks the
        # password as a SecretStr, so it cannot appear here.
        msg = str(e).replace("\n", " ")
        print(f"Error: {msg[:500]}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
