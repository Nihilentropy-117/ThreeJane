---
name: todoist
description: >
  Add tasks to Todoist and search existing tasks by any field. Use this skill
  whenever the user mentions Todoist, wants to add a task/reminder/todo, or asks
  to find, look up, or check something in their task list. Trigger even on casual
  phrasing like "remind me to...", "add to my todo list", "what's on my list for...",
  "do I have anything about...", or "search my tasks for...". The skill handles
  fuzzy matching, so typos and approximate queries work fine.
---

## Setup

**API key:** Read from `TODOIST_API_KEY` environment variable.  
**Script:** `$SKILL_DIR/scripts/todoist.py`  
**Dependencies:** `todoist-api-python`, `thefuzz` (pre-installed in `/venv`)

---

## Searching tasks

```bash
python $SKILL_DIR/scripts/todoist.py search "query" [--limit N]
```

The script fuzzy-matches the query against every task's **name, description, project name, labels, and due date**. Results are sorted by match score and returned as JSON.

**Example queries you might construct:**
- `"birthday"` → finds all birthday reminders
- `"april"` or `"2026-04"` → finds tasks due in April
- `"house shopping"` → finds tasks in the House or Shopping List projects
- `"cat litter"` → finds litter-related recurring tasks
- `"urgent consequential"` → finds tasks with those labels

**Output fields:** `id`, `content`, `description`, `project`, `due`, `labels`, `priority`, `match_score`

A `match_score` of 80+ is a strong match. 50–79 is a loose match. Below 50 is probably noise — use judgment about whether to show those results.

**Presenting results:** Show as a clean list. Include project name and due date when present. Skip match_score in the output to the user. If there are many weak results, show only the top 5–7 and mention how many total were found.

---

## Adding tasks

```bash
python $SKILL_DIR/scripts/todoist.py add "Task content" \
  [--description "Optional details"] \
  [--due "natural language date, e.g. 'every monday' or 'Apr 19'"] \
  [--project "Project name"] \
  [--labels "Label1,Label2"] \
  [--priority 1-4]
```

**Priority levels:** 1 = normal, 2 = medium, 3 = high, 4 = urgent (Todoist displays these in reverse — p4 is their "Priority 1")

**Project matching** is fuzzy — "shoping list" will match "Shopping List". If no project is specified, the task goes to Inbox.

**Inferring parameters from the user's request:**
- "add 'fix doorbell' to the House project" → `add "Fix doorbell" --project "House"`
- "remind me every monday to check in with Kas" → `add "Check in with Kas" --due "every monday"`
- "urgent task: pay mortgage by the 1st" → `add "Pay mortgage" --due "every 1st" --priority 4`
- If the user just says "add X" with no other details, don't ask — just add it to Inbox

**After adding:** Confirm to the user what was created, including the project it landed in, the due date if one was set, and the `url` from the result as a clickable link so they can open it directly in Todoist.

---

## Error handling

- If `TODOIST_API_KEY` is not set, the script exits with an error message. Tell the user to set the variable.
- If a project name doesn't match well enough (< 55% similarity), the task goes to Inbox with a warning. Tell the user which project you were looking for and that it defaulted to Inbox.
