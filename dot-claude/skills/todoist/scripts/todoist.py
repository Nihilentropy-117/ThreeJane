#!/usr/bin/env python3
"""
Todoist CLI helper for Claude Code.

Usage:
  python todoist.py search "query" [--limit N]
  python todoist.py add "task content" [--description "..."] [--due "tomorrow"] [--project "Name"] [--labels "l1,l2"] [--priority 1-4]

Outputs JSON to stdout. Errors/warnings go to stderr.
Reads TODOIST_API_KEY from environment.
"""

import sys
import os
import re
import json
import argparse
import unicodedata
from todoist_api_python.api import TodoistAPI
from thefuzz import fuzz, process


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s]+", "-", text).strip("-")


def _task_url(task_id: str, content: str | None = None) -> str:
    slug = _slugify(content) if content else ""
    path = f"{slug}-{task_id}" if slug else task_id
    return f"https://app.todoist.com/app/task/{path}"


def get_api():
    key = os.environ.get('TODOIST_API_KEY')
    if not key:
        print("Error: TODOIST_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return TodoistAPI(key)


def flatten(paginator):
    """Flatten a ResultsPaginator (each page is itself a list) into a flat list."""
    items = []
    for page in paginator:
        if isinstance(page, list):
            items.extend(page)
        else:
            items.append(page)
    return items


def cmd_search(args):
    api = get_api()
    query = args.query.lower()
    limit = args.limit

    tasks = flatten(api.get_tasks())
    projects = {p.id: p.name for p in flatten(api.get_projects())}

    results = []
    for task in tasks:
        project_name = projects.get(task.project_id, '')
        due_str = str(task.due.date) if task.due else ''
        labels_str = ' '.join(task.labels)

        # Score against multiple fields independently so a due-date search
        # doesn't require the query to also appear in the task name.
        scores = [
            fuzz.partial_ratio(query, task.content.lower()),
            fuzz.token_sort_ratio(query, task.content.lower()),
            fuzz.partial_ratio(query, (task.description or '').lower()),
            fuzz.partial_ratio(query, project_name.lower()),
            fuzz.partial_ratio(query, labels_str.lower()),
            fuzz.partial_ratio(query, due_str),
        ]
        score = max(scores)
        results.append((score, task, project_name))

    results.sort(key=lambda x: x[0], reverse=True)

    output = []
    for score, task, project_name in results[:limit]:
        output.append({
            'id': task.id,
            'content': task.content,
            'description': task.description or None,
            'project': project_name,
            'due': str(task.due.date) if task.due else None,
            'labels': task.labels,
            'priority': task.priority,
            'url': _task_url(task.id, task.content),
            'match_score': score,
        })

    print(json.dumps(output, indent=2, default=str))


def cmd_add(args):
    api = get_api()

    project_id = None
    if args.project:
        name_to_id = {p.name: p.id for p in flatten(api.get_projects())}
        match = process.extractOne(args.project, list(name_to_id.keys()), scorer=fuzz.ratio)
        if match and match[1] >= 55:
            project_id = name_to_id[match[0]]
            if match[0].lower() != args.project.lower():
                print(f"Note: matched project '{args.project}' → '{match[0]}'", file=sys.stderr)
        else:
            print(f"Warning: no project matched '{args.project}' (threshold 55%). Adding to Inbox.", file=sys.stderr)

    kwargs = {'content': args.content}
    if args.description:
        kwargs['description'] = args.description
    if args.due:
        kwargs['due_string'] = args.due
    if project_id:
        kwargs['project_id'] = project_id
    if args.labels:
        kwargs['labels'] = [l.strip() for l in args.labels.split(',')]
    if args.priority:
        kwargs['priority'] = args.priority

    task = api.add_task(**kwargs)
    result = {
        'id': task.id,
        'content': task.content,
        'project': args.project or 'Inbox',
        'due': str(task.due.date) if task.due else None,
        'labels': task.labels,
        'url': _task_url(task.id, task.content),
    }
    print(json.dumps(result, indent=2, default=str))
    print(f"✓ Added: \"{task.content}\"", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd')

    p_search = sub.add_parser('search')
    p_search.add_argument('query')
    p_search.add_argument('--limit', type=int, default=10)

    p_add = sub.add_parser('add')
    p_add.add_argument('content')
    p_add.add_argument('--description', default=None)
    p_add.add_argument('--due', default=None)
    p_add.add_argument('--project', default=None)
    p_add.add_argument('--labels', default=None, help='Comma-separated labels')
    p_add.add_argument('--priority', type=int, choices=[1, 2, 3, 4], default=None)

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
