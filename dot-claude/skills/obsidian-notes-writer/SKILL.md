---
name: obsidian-notes-writer
description: Write new notes into the user's Obsidian vault in the correct format and location. Use this skill whenever the user asks to save, add, create, or log something into Obsidian — whether it's a quote, a place, a recipe, a media entry, a journal entry, a person note, or any other note type. Trigger even on casual phrasing like "add this to my notes", "save this quote", "log this restaurant", "remember this recipe", or "put this in Obsidian".
---

# Obsidian Notes Writer

Write notes into the user's Obsidian vault. The vault is mostly read-only — the **only writable location** is the `intake/` folder at the vault root.

## Vault

- **Default vault**: `WanderlandReX`
- **Vault path**: `/user-files/notes/WanderlandReX/`
- **Write destination**: `/user-files/notes/WanderlandReX/intake/`
- Use a different vault only if the user explicitly says so.

Notes dropped into `intake/` are picked up by Obsidian's sync and moved to the right folder by the user later, so don't worry about filing them into subfolders — just get the content and format right.

## Note Types and Formats

Each note type has its own frontmatter schema and body structure. Match the type to what the user is asking to save.

---

### Quotes

**Frontmatter fields:** `author`, `date_added`, `medium`, `source` (source is the title of the book/show/etc — omit if unknown)

**Medium options:** `Fiction Book`, `Non-Fiction Book`, `TV Show`, `Movie`, `Speech`, `Song`, `Poem`, `Article`

**Title:** The first ~8 words of the quote followed by `...`

**Body:** The full quote text, no extra formatting.

```markdown
---
author: Terry Pratchett
date_added: "April 5, 2026"
medium: Fiction Book
source: Night Watch
---

# Sam Vimes felt like a class traitor...

Sam Vimes felt like a class traitor every time he wore it.
```

---

### Places

**Frontmatter fields:** `location` (full street address), `status` (`Not Visited` or `Visited`), `type` (comma-separated descriptors), optionally `life_context`, `tags`

**Title:** The place name

**Body:** One punchy descriptive paragraph — evocative, informative, personal if context warrants it.

```markdown
---
location: "113 W McMillan St, Cincinnati, OH 45219"
status: Not Visited
type: Pizza; Sicilian; Casual; Late Night
---

# Adriatico's

Beloved UC-area pizza parlor famous for thick-crust Sicilian pies and the massive 30-slice Bearcat size.
```

---

### Recipes

**Frontmatter fields:** `course` (Appetizer / Soup / Salad / Main / Side / Dessert / Snack / Drink), `cuisine`, `tags`

**Body structure:**
- `# Recipe Name`
- `# Ingredients` — bullet list, amounts first
- `# Instructions` — numbered steps

```markdown
---
course: Main
cuisine: Thai
tags: Wanderland
---

# Chicken and Pineapple Green Curry

# Ingredients

- 1 can coconut milk
- 1 lb chicken breast, bite-sized

# Instructions

1. Simmer half the coconut milk over low heat for 5 minutes.
2. Add curry paste and cook 1-2 minutes, then add remaining coconut milk.
```

---

### Media (Books, Movies, TV Shows, etc.)

**Frontmatter fields:** `author_creator`, `release_date` (YYYY-MM-DD or "January 1, YYYY"), `status` (`To Do` / `In Progress` / `Done`), `type` (`Book` / `Movie` / `TV Show` / `Podcast` / `Album` / `Game`)

**Title:** The media title

**Body:** One sharp sentence or short paragraph — the user's own take, vivid and opinionated. Not a Wikipedia synopsis. Capture what makes it distinctive or why it matters.

```markdown
---
author_creator: Alfonso Cuarón
release_date: "January 1, 2006"
status: To Do
type: Movie
---

# Children of Men

A collapsing society, mass infertility, and the quiet horror of hope in a world that gave up years ago.
```

---

### Journal Entries

**No frontmatter.**

**Title:** The date as `# YYYY-MM-DD` (or the user's preferred format if they give one)

**Body:** Bullet points for events/notes, or free prose for reflections. Keep it terse and honest — this is a personal log, not a diary performance.

```markdown
# 2026-04-05

- Went to the farmers market
- Started reading Hyperion again
- Talked to Nina about the Iceland thing
```

---

### People Notes

**No frontmatter** (usually).

**Title:** `# Full Name`

**Body:** Sections with `##` headers for different aspects (`## Vitals`, `## Politics`, `## Misc`, etc.). Mix of bullet points and casual prose. Write it in the user's voice — direct, personal, no polish required.

```markdown
# Jane Smith

## Vitals

Jane Smith, 2/14/1990

## Misc

- Wants to move to Portugal someday
- Favorite animal: capybara
```

---

## Filename Convention

Use the note title as the filename, with spaces preserved (Obsidian handles these fine). For journal entries use `YYYY-MM-DD.md`. For quotes, use the first ~8 words of the quote as the filename.

## Writing Style Notes

- Match the user's voice: casual, direct, no corporate polish
- Lowercase is fine in body text
- Don't over-explain — short and punchy beats thorough and dull
- Bullet points for lists, free prose for thoughts
- Strikethrough (`~~text~~`) for outdated info in people notes
- No need to add tags or wikilinks unless the user asks

## Workflow

1. Identify the note type from context
2. Collect any missing required fields (ask only if truly needed — infer where possible)
3. Write the note to `/user-files/notes/WanderlandReX/intake/<filename>.md`
4. Confirm to the user what was saved and where
