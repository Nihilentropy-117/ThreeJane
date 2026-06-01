#!/usr/bin/env python3
"""
TickTick CLI helper for 3Jane.

Usage:
  python ticktick.py search "query" [--on DATE] [--limit N]
  python ticktick.py add "task title" [--description "..."] [--due DATE] \
      [--project "Name"] [--labels "tag1,tag2"] [--priority 0|1|3|5] [--repeat "RRULE:..."]

Outputs JSON to stdout. Errors/warnings go to stderr.
Reads TICKTICK_ACCESS_TOKEN from environment (an OAuth2 access token for the
TickTick Open API, https://developer.ticktick.com).

Uses only the standard library plus `thefuzz` (pre-installed in the image).
"""

import sys
import os
import re
import json
import datetime
import argparse
import urllib.request
import urllib.error
from thefuzz import fuzz, process

API_BASE = "https://api.ticktick.com/open/v1"


def get_token():
    tok = os.environ.get("TICKTICK_ACCESS_TOKEN")
    if not tok:
        print("Error: TICKTICK_ACCESS_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return tok


def _request(method, path, token, body=None):
    """Make a TickTick Open API request and return the parsed JSON body."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API_BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if e.code in (401, 403):
            detail += " (check TICKTICK_ACCESS_TOKEN — it may be missing scopes or expired)"
        print(f"Error: TickTick API {e.code} on {method} {path}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: could not reach TickTick API: {e}", file=sys.stderr)
        sys.exit(1)


def get_projects(token):
    """Return the list of (non-Inbox) project objects."""
    projects = _request("GET", "/project", token)
    return projects if isinstance(projects, list) else []


def get_all_tasks(token, projects):
    """Fetch all uncompleted tasks across every project."""
    tasks = []
    for p in projects:
        data = _request("GET", f"/project/{p['id']}/data", token)
        tasks.extend(data.get("tasks") or [])
    return tasks


_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_REL_DAYS = {'today': 0, 'tomorrow': 1, 'tomorow': 1, 'yesterday': -1}


def resolve_date(s: str) -> str:
    """Resolve a date expression to an ISO date string (YYYY-MM-DD), computed
    from the local clock. Accepts an ISO date, the words today/tomorrow/yesterday,
    or a signed day offset like '+1' / '-2'. Date math is done here, not by the
    caller, so relative days are always correct."""
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


def task_due_date(task) -> str:
    """The YYYY-MM-DD portion of a task's dueDate, or '' if it has none.
    TickTick returns dueDate as an ISO datetime like '2026-04-19T00:00:00.000+0000'."""
    due = task.get("dueDate")
    return due[:10] if due and len(due) >= 10 else ""


def _task_url(task) -> str:
    pid = task.get("projectId")
    tid = task.get("id")
    if pid and tid:
        return f"https://ticktick.com/webapp/#p/{pid}/tasks/{tid}"
    return f"https://ticktick.com/webapp/#q/all/tasks/{tid}" if tid else "https://ticktick.com/webapp/"


def _fmt_task(task, proj_name, score):
    """Shape a task into the output schema (mirrors the Todoist skill's fields)."""
    return {
        'id': task.get('id'),
        'content': task.get('title'),
        'description': (task.get('content') or task.get('desc') or None),
        'project': proj_name.get(task.get('projectId', ''), ''),
        'due': task_due_date(task) or None,
        'labels': task.get('tags') or [],
        'priority': task.get('priority', 0),
        'url': _task_url(task),
        'match_score': score,
    }


def cmd_search(args):
    token = get_token()
    query = (args.query or '').lower()
    limit = args.limit
    if not query and not args.on:
        print("Error: provide a search query or --on DATE.", file=sys.stderr)
        sys.exit(1)

    projects = get_projects(token)
    proj_name = {p['id']: p['name'] for p in projects}
    tasks = get_all_tasks(token, projects)

    # Exact due-date search. Use --on for relative/absolute dates (resolved here so
    # "tomorrow" is always correct), or a bare ISO-date query. Fuzzy scoring treats
    # adjacent dates (…-01 vs …-02) as near matches, so it can't be trusted here.
    target_date = None
    if args.on:
        target_date = resolve_date(args.on)
    elif _ISO_DATE_RE.match(query):
        target_date = query
    if target_date:
        output = [_fmt_task(t, proj_name, 100) for t in tasks if task_due_date(t) == target_date]
        print(json.dumps(output[:limit], indent=2, default=str))
        return

    results = []
    for task in tasks:
        project_name = proj_name.get(task.get('projectId', ''), '')
        due_str = task_due_date(task)
        labels_str = ' '.join(task.get('tags') or [])
        title = (task.get('title') or '').lower()
        body = (task.get('content') or task.get('desc') or '').lower()

        # Score against multiple fields independently so a due-date search
        # doesn't require the query to also appear in the task title.
        scores = [
            fuzz.partial_ratio(query, title),
            fuzz.token_sort_ratio(query, title),
            fuzz.partial_ratio(query, body),
            fuzz.partial_ratio(query, project_name.lower()),
            fuzz.partial_ratio(query, labels_str.lower()),
            fuzz.partial_ratio(query, due_str),
        ]
        results.append((max(scores), task))

    results.sort(key=lambda x: x[0], reverse=True)
    output = [_fmt_task(task, proj_name, score) for score, task in results[:limit]]
    print(json.dumps(output, indent=2, default=str))


def cmd_add(args):
    token = get_token()

    project_id = None
    project_label = 'Inbox'
    if args.project:
        name_to_id = {p['name']: p['id'] for p in get_projects(token)}
        match = (process.extractOne(args.project, list(name_to_id.keys()), scorer=fuzz.ratio)
                 if name_to_id else None)
        if match and match[1] >= 55:
            project_id = name_to_id[match[0]]
            project_label = match[0]
            if match[0].lower() != args.project.lower():
                print(f"Note: matched project '{args.project}' → '{match[0]}'", file=sys.stderr)
        else:
            print(f"Warning: no project matched '{args.project}' (threshold 55%). Adding to Inbox.",
                  file=sys.stderr)

    body = {'title': args.content}
    if args.description:
        body['content'] = args.description
    if args.due:
        try:
            iso = resolve_date(args.due)
        except ValueError:
            print(f"Error: --due '{args.due}' isn't a concrete date. TickTick's API needs an absolute "
                  f"date (ISO YYYY-MM-DD, today/tomorrow/yesterday, or +N/-N days). For a recurring task "
                  f"use --repeat with an RRULE, e.g. --repeat 'RRULE:FREQ=WEEKLY;BYDAY=MO'.",
                  file=sys.stderr)
            sys.exit(1)
        body['dueDate'] = f"{iso}T00:00:00+0000"
        body['isAllDay'] = True
    if project_id:
        body['projectId'] = project_id
    if args.labels:
        body['tags'] = [t.strip() for t in args.labels.split(',') if t.strip()]
    if args.priority is not None:
        body['priority'] = args.priority
    if args.repeat:
        body['repeatFlag'] = args.repeat

    task = _request("POST", "/task", token, body)
    result = {
        'id': task.get('id'),
        'content': task.get('title'),
        'project': project_label,
        'due': task_due_date(task) or None,
        'labels': task.get('tags') or [],
        'url': _task_url(task),
    }
    print(json.dumps(result, indent=2, default=str))
    print(f'✓ Added: "{task.get("title")}"', file=sys.stderr)


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
    p_add.add_argument('--labels', default=None, help='Comma-separated tags')
    p_add.add_argument('--priority', type=int, choices=[0, 1, 3, 5], default=None,
                       help='TickTick priority: 0 none, 1 low, 3 medium, 5 high')
    p_add.add_argument('--repeat', default=None, help="RRULE for recurring tasks, e.g. RRULE:FREQ=WEEKLY;BYDAY=MO")

    args = parser.parse_args()
    if args.cmd == 'search':
        cmd_search(args)
    elif args.cmd == 'add':
        cmd_add(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
