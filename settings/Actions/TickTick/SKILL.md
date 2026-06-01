---
name: ticktick
description: >
  Add tasks to TickTick and search existing tasks by any field. Use this skill
  whenever the user mentions TickTick, wants to add a task/reminder/todo, or asks
  to find, look up, or check something in their task list. Trigger even on casual
  phrasing like "remind me to...", "add to my todo list", "what's on my list for...",
  "do I have anything about...", or "search my tasks for...". The skill handles
  fuzzy matching, so typos and approximate queries work fine.
---

## Setup

**Access token:** Read from `TICKTICK_ACCESS_TOKEN` environment variable. This is an
OAuth2 access token for the TickTick Open API (<https://developer.ticktick.com>),
minted once by authorizing the app and stored in `.env`.  
**Script:** `$SKILL_DIR/scripts/ticktick.py` (run with plain `python`).  
**Dependencies:** `thefuzz` (pre-installed in the image). The script uses only the
standard library otherwise.

---

## Searching tasks

```bash
python $SKILL_DIR/scripts/ticktick.py search "query" [--limit N]
```

The script fuzzy-matches the query against every task's **title, description/note, project name, tags, and due date**. Results are sorted by match score and returned as JSON.

**Example queries you might construct:**
- `"birthday"` → finds all birthday reminders
- `"april"` or `"2026-04"` → finds tasks due in April
- `"house shopping"` → finds tasks in the House or Shopping List projects
- `"cat litter"` → finds litter-related recurring tasks
- `"urgent errands"` → finds tasks tagged or named accordingly

**Searching by due date — use `--on`, do NOT compute dates yourself:**

```bash
python /app/settings/Actions/TickTick/scripts/ticktick.py search --on tomorrow
```

`--on` resolves the date from the system clock and matches the due date exactly,
so relative days are always correct. Pass the relative word directly rather than
working out the calendar date in your head (that arithmetic is error-prone):
- "what am I doing tomorrow?" → `search --on tomorrow`
- "anything today?" → `search --on today`
- a specific day → `search --on 2026-06-15` (ISO) or an offset like `--on +3`
- a range ("this weekend", "next week") → run one `--on` search per day
  (e.g. `--on +5` then `--on +6`) and combine the results.

(A bare ISO-date query like `search "2026-06-15"` also does an exact match, but
prefer `--on` so you never have to compute the date.)

**Output fields:** `id`, `content` (the task title), `description`, `project`, `due`, `labels` (TickTick tags), `priority`, `match_score`

A `match_score` of 80+ is a strong match. 50–79 is a loose match. Below 50 is probably noise — use judgment about whether to show those results.

**Presenting results:** Show as a clean list. Include project name and due date when present. Skip match_score in the output to the user. If there are many weak results, show only the top 5–7 and mention how many total were found.

---

## Adding tasks

```bash
python $SKILL_DIR/scripts/ticktick.py add "Task title" \
  [--description "Optional note"] \
  [--due "ISO date, today/tomorrow/yesterday, or +N/-N days"] \
  [--project "Project name"] \
  [--labels "Tag1,Tag2"] \
  [--priority 0|1|3|5] \
  [--repeat "RRULE:FREQ=WEEKLY;BYDAY=MO"]
```

**Priority levels (TickTick's native scale):** 0 = none, 1 = low, 3 = medium, 5 = high.

**Due dates:** TickTick's Open API takes a concrete date, not natural language. Pass an
ISO date, `today`/`tomorrow`/`yesterday`, or a `+N`/`-N` day offset — the script resolves
it from the clock. For **recurring** tasks ("every monday", "every 1st"), TickTick has no
natural-language parser like Todoist; use `--repeat` with an RFC-5545 RRULE instead:
- "every monday" → `--repeat "RRULE:FREQ=WEEKLY;BYDAY=MO"`
- "every day" → `--repeat "RRULE:FREQ=DAILY"`
- "the 1st of each month" → `--due "every 1st"` is NOT valid; use `--repeat "RRULE:FREQ=MONTHLY;BYMONTHDAY=1"` (optionally with a `--due` start date).

**Labels** map to TickTick **tags** (comma-separated).

**Project matching** is fuzzy — "shoping list" will match "Shopping List". If no project is specified, the task goes to Inbox.

**Inferring parameters from the user's request:**
- "add 'fix doorbell' to the House project" → `add "Fix doorbell" --project "House"`
- "remind me every monday to check in with Kas" → `add "Check in with Kas" --repeat "RRULE:FREQ=WEEKLY;BYDAY=MO"`
- "urgent task: pay mortgage on the 1st" → `add "Pay mortgage" --repeat "RRULE:FREQ=MONTHLY;BYMONTHDAY=1" --priority 5`
- If the user just says "add X" with no other details, don't ask — just add it to Inbox

**After adding:** Confirm to the user what was created, including the project it landed in, the due date if one was set, and the `url` from the result as a clickable link so they can open it directly in TickTick.

---

## Error handling

- If `TICKTICK_ACCESS_TOKEN` is not set, the script exits with an error message. Tell the user to set the variable.
- A `401`/`403` from the API means the token is missing, expired, or lacks scopes — tell the user to re-authorize and refresh `TICKTICK_ACCESS_TOKEN`.
- If a project name doesn't match well enough (< 55% similarity), the task goes to Inbox with a warning. Tell the user which project you were looking for and that it defaulted to Inbox.
- **Inbox search gap:** TickTick's Open API only lists named projects, so `search` does not see tasks sitting in the **Inbox**. Tasks can be *added* to the Inbox, but to find them later they need to live in a project. Mention this if a search comes up empty and the user expected an Inbox task.
