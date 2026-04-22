---
name: obsidian-base
description: Query and manipulate Obsidian vaults via a bundled SQL-like CLI (`obsidian-base`). Use this skill whenever the user refers to one of their personal Obsidian databases — phrases like "my places database", "my media database", "my recipes database", "my quotes base", "check my reading list", "add a restaurant to my vault", or similar. Also use it proactively whenever you encounter a `.base` file on disk (typically under `/user-files/notes/`) or whenever you're already operating inside an Obsidian vault and the user wants to read, filter, aggregate, insert, update, or delete notes by property, tag, or folder. Prefer this skill over manual `grep`/`find`/YAML parsing for any vault question more complex than "show me this one file".
---

# obsidian-base

A bundled CLI that turns each `.base` file in an Obsidian vault into a virtual SQL table. You write SQL; it reads the base's global filters, applies them to the vault's markdown notes, and projects frontmatter / file properties / formula properties as columns.

## When this applies

- The user says anything like *"my X database"*, *"my X base"*, *"my reading list database"* — these almost always map to a `.base` file.
- You encounter a `.base` file while browsing.
- You are already working inside `/user-files/notes/` and the task involves reading/editing notes matching some criteria.
- The user wants statistics over notes: counts, averages, groupings, "how many of X have Y".

If the task is a single-file read ("open my Bite.md"), just read the file — don't invoke this skill.

## Setup

The binary is bundled at `bin/obsidian-base` relative to this skill. Invoke it directly — no install needed.

```bash
"$CLAUDE_SKILL_DIR/bin/obsidian-base" --vault <vault-path> <command>
```

### Vault locations

Active vaults live under `/user-files/notes/`. Each subdirectory is a vault, though some have a nested layout.

**To find the right vault path:** run `bases` pointing at the parent and it'll walk recursively; or list `/user-files/notes/*/` and drill down until you find `.base` files. Once you know the vault root, pass it as `--vault`.

You can also set `OBSIDIAN_VAULT=<path>` once for the shell session and drop the flag.


## The three commands you'll use most

### 1. Discover what bases exist

```bash
obsidian-base --vault <path> bases
```

Returns JSON listing every `.base` file with its name, path, view count, and global filter. Start here when you don't know what's available.

### 2. Understand a base's schema

```bash
obsidian-base --vault <path> describe <base-name>
```

Returns the columns (with inferred types), formulas, views, and note count. Always `describe` before writing a SQL query against an unfamiliar base — it tells you the exact column names, which are case-sensitive.

Add `--raw` to dump the `.base` YAML verbatim if you need the original filter expressions.

### 3. Query with SQL

```bash
obsidian-base --vault <path> sql "<SQL statement>"
```

Default output is JSON. Add `--format table` for terminal-friendly output, `--format csv` for spreadsheets, `--format yaml` for human reading.

## SQL dialect — what's supported

The base name is the table. Columns come from three sources:

| Prefix | Source | Example |
| --- | --- | --- |
| `file.*` | File metadata | `file.name`, `file.path`, `file.folder`, `file.mtime`, `file.size`, `file.tags`, `file.links` |
| none / `note.*` | Frontmatter YAML | `rating`, `status`, `tags`, `location` |
| `formula.*` | Formulas defined in the `.base` file | `formula.visit_count` |

**Standard SQL that works:** `SELECT`, `FROM`, `WHERE`, `AND`, `OR`, `NOT`, `ORDER BY`, `LIMIT`, `OFFSET`, `GROUP BY`, `INSERT`, `UPDATE`, `DELETE`. Operators: `=`, `!=`, `<`, `<=`, `>`, `>=`.

**Obsidian-specific extensions** — use these when keyword matching isn't enough:

| Syntax | What it does |
| --- | --- |
| `col CONTAINS 'x'` | List contains item, or string contains substring |
| `col CONTAINS_ALL('a','b')` | List contains all of these |
| `col CONTAINS_ANY('a','b')` | List contains at least one |
| `col STARTS_WITH 'x'` / `ENDS_WITH 'x'` | String prefix/suffix |
| `col LIKE '%pattern%'` | SQL-style wildcard match |
| `col MATCHES '/regex/'` | Regex match |
| `col IS EMPTY` / `IS NOT EMPTY` | Property missing or empty |
| `HAS_TAG('brunch')` | Note has this tag (frontmatter or inline `#tag`) |
| `HAS_TAG('a', 'b')` | Has all listed tags |
| `IN_FOLDER('Files/Places')` | Note lives in this folder |
| `HAS_LINK('Other Note')` | Body or frontmatter links to this note |
| `HAS_PROPERTY('rating')` | Frontmatter has this key |

**Aggregates:** `COUNT(*)`, `SUM`, `AVG`, `MIN`, `MAX`, `MEDIAN`, `STDDEV`, `RANGE`, `EARLIEST`, `LATEST`, `CHECKED`, `UNCHECKED`, `EMPTY`, `FILLED`, `UNIQUE`. Work with `GROUP BY` for per-category breakdowns.

## Proximity / location-based search (Places database)

Places notes store `lat` and `lon` as frontmatter. Use these for any "near me", "nearby", or city-radius query instead of string-matching on `location`.

### Step 1 — geocode the reference point

Use Nominatim to turn a city name or address into lat/lon:

```bash
curl -s -H "User-Agent: WanderlandReX-geocoder/1.0 (gray.lott@live.com)" \
  "https://nominatim.openstreetmap.org/search?q=Cincinnati+OH&format=json&limit=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['lat'], d[0]['lon'])"
```

URL-encode spaces as `+` or `%20`. Always include the `User-Agent` header.

### Step 2 — query with Haversine math

The obsidian-base SQL engine supports arithmetic, so compute approximate distance in miles inline. Use a bounding-box pre-filter (cheap) then a precise Haversine expression (exact):

```sql
-- ~50-mile radius around Cincinnati (lat=39.1031, lon=-84.5120)
-- 1 degree lat ≈ 69 miles; 1 degree lon ≈ 69*cos(lat) miles
-- Haversine (approximate, sufficient for <200 miles):
--   d = 69 * sqrt((lat-REF_LAT)^2 + ((lon-REF_LON)*cos(REF_LAT*pi()/180))^2)
SELECT file.name, type, status, location,
       69 * sqrt((lat - 39.1031)*(lat - 39.1031) + ((lon - (-84.5120))*0.7771)*((lon - (-84.5120))*0.7771)) AS miles
FROM Places
WHERE lat IS NOT EMPTY
  AND lat > 38.4 AND lat < 39.8
  AND lon > -85.2 AND lon < -83.8
ORDER BY miles ASC
```

Replace `REF_LAT` / `REF_LON` with the geocoded coordinates. `cos(39.1°) ≈ 0.7771` — recompute for very different latitudes (`cos(lat_deg * π/180)`).

### Proximity + attribute filter

```sql
SELECT file.name, type, status,
       69 * sqrt((lat-39.1031)^2 + ((lon+84.5120)*0.7771)^2) AS miles
FROM Places
WHERE lat IS NOT EMPTY
  AND (type CONTAINS 'Vegetarian' OR type CONTAINS 'Vegan')
  AND lat > 38.4 AND lat < 39.8 AND lon > -85.2 AND lon < -83.8
ORDER BY miles ASC
```

### Radius ↔ bounding-box conversion

| Radius | Lat delta | Lon delta (≈Cincinnati) |
|--------|-----------|------------------------|
| 10 mi  | ±0.14°    | ±0.18°                 |
| 25 mi  | ±0.36°    | ±0.46°                 |
| 50 mi  | ±0.72°    | ±0.92°                 |

### Adding lat/lon to a new place

Always geocode on INSERT:
```bash
ADDR="1142 Main St Cincinnati OH"
COORDS=$(curl -s -H "User-Agent: WanderlandReX-geocoder/1.0 (gray.lott@live.com)" \
  "https://nominatim.openstreetmap.org/search?q=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote_plus(sys.argv[1]))" "$ADDR")&format=json&limit=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['lat'], d[0]['lon'])")
LAT=$(echo $COORDS | cut -d' ' -f1)
LON=$(echo $COORDS | cut -d' ' -f2)
# Then include lat and lon in your INSERT statement
```

---

## Common patterns

**"Find all X where Y"** — the bread-and-butter query:
```bash
obsidian-base --vault ~/Obsidian/WanderlandRex/WanderlandReX \
  sql "SELECT file.name, rating, location FROM Places
       WHERE tags CONTAINS 'brunch' AND status = 'Not Visited'
       ORDER BY rating DESC LIMIT 10"
```

**"How many of my X are Y?"** — counts and aggregates:
```bash
obsidian-base --vault <path> sql "SELECT COUNT(*) FROM Media WHERE status = 'To Do'"
obsidian-base --vault <path> sql "SELECT status, COUNT(*) FROM Places GROUP BY status"
obsidian-base --vault <path> sql "SELECT AVG(rating), MEDIAN(rating) FROM Places WHERE status = 'Visited'"
```

**"Add a new X"** — INSERT. The `name` column becomes the filename; everything else becomes frontmatter. The new note must satisfy the base's global filters or the insert is rejected:
```bash
obsidian-base --vault <path> sql \
  "INSERT INTO Places (name, tags, rating, location, status)
   VALUES ('Brunch Spot', '[\"brunch\",\"mimosas\"]', 4.5, 'Cincinnati, OH', 'Not Visited')"
```

Pass list/object values as JSON strings (double-quote the values). Use `--folder <subdir>` to override where the note lands (default: same folder as the `.base` file).

**"Update X's rating"** — UPDATE modifies frontmatter in place, preserving body and other fields:
```bash
obsidian-base --vault <path> sql \
  "UPDATE Places SET status = 'Visited', rating = 4.8 WHERE file.name = 'Bite'"
```

**"Delete X"** — DELETE is a two-step confirmation by default. Without `--yes`, it reports how many would be deleted. With `--yes` and no `--hard-delete`, it *unlinks* (clears the frontmatter so the note no longer matches the base filters but the file survives). With `--hard-delete --yes`, the file is actually removed:
```bash
# Preview
obsidian-base --vault <path> sql "DELETE FROM Media WHERE status = 'archived'"
# Unlink
obsidian-base --vault <path> sql --yes "DELETE FROM Media WHERE status = 'archived'"
# Hard delete
obsidian-base --vault <path> sql --yes --hard-delete "DELETE FROM Media WHERE status = 'archived'"
```

## Managing bases

Occasionally you'll want to create or reshape a base:

```bash
# Create a new base
obsidian-base --vault <path> init reviews \
  --filter 'file.hasTag("review")' --folder Files/Reviews --type table

# Add a formula column
obsidian-base --vault <path> alter Places \
  --add-formula 'visit_count: "visits.length"'

# Swap the global filter
obsidian-base --vault <path> alter Places --set-filter 'file.hasTag("place")'

# Other alter flags: --remove-formula, --add-view (JSON), --remove-view,
# --set-property 'rating:displayName=Stars', --add-summary 'avg: Average'
```

`--filter` for `init` and `--set-filter` for `alter` take **Bases expression syntax** (e.g., `file.hasTag("x")`, `file.folder == "Files/X"`), not SQL. That's how the `.base` YAML stores filters, so pass them through verbatim.

## Error handling

Errors come out on stderr as a single JSON object:
```json
{"error": "base_not_found", "message": "...", "suggestion": "..."}
```

When you see `base_not_found`, the `suggestion` field lists available bases — use that to fix the name. When you see `filter_mismatch` on an INSERT, the proposed note doesn't satisfy the base's global filter (e.g., wrong folder) — either adjust the `--folder` flag or add the missing tag/property.

## Tips for getting it right

- **Column names are case-sensitive.** `describe` the base first if you're unsure. Obsidian frontmatter is conventionally lowercase but don't assume — the user's notes might use `Status` or `Rating`.
- **Tags live in two places.** Frontmatter `tags: [a, b]` *and* inline `#tag` in the body both count. `file.tags` and `HAS_TAG(...)` see both.
- **Dates.** Frontmatter date strings are parsed automatically where possible (ISO 8601 preferred). If a comparison is giving wrong results, check the raw value via `SELECT file.name, my_date FROM ...` — it might be a plain string.
- **JSON-encoded list values in INSERT/UPDATE.** To set a list column, pass a string that's valid JSON (`'["a","b"]'`). The CLI auto-parses.
- **The `Quotes` and `Recipies` bases in the sample vault have no defined frontmatter schema** — `describe` returns only `file.name`. For bases like that, queries by filename / folder are usually what you want.
