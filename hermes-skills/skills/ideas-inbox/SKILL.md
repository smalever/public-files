---
name: ideas-inbox
description: "Use when the owner says «запиши идею/мысль» — quick capture to /notes/Идеи."
version: 2.2.0
author: Viktor Filatov (smalever), Hermes Agent
license: CC-BY-4.0
metadata:
  hermes:
    tags: [ideas, inbox, obsidian, note-taking, capture]
    related_skills: [obsidian]
---

# Ideas Inbox

## Overview

Use this skill to quickly capture raw ideas before they are lost. The goal is not to fully design or evaluate the idea, but to preserve it in a short, readable form so the user can return to it later.

Destination: `/notes/Идеи/<Короткое название>.md`, then add one line `- [[<Название>]] — <суть>` to the matching section of `/notes/Идеи/Идеи.md` (create a new logical heading if no existing section fits, e.g. `## Книги и чтение`).

Vault mechanics (paths, file tools, wikilinks) — see the `obsidian` skill. Note template — `/notes/Шаблоны/Идея.md`.

Before saving, load `notes-vault` and read `/notes/AGENTS.md`; preserve the vault's secret and protected-data constraints.

## When to Use

Use when the user says things like:
- "запиши идею"
- "сохрани мысль"
- "закинь в идеи"
- "потом к этому вернёмся"
- "вот мысль, чтобы не потерять"

Do not use for:
- song, track or music ideas — if the owner has a dedicated songwriting skill, follow that skill instead
- full project planning
- deep technical design
- task tracking / TODO lists
- research summaries

## Capture first

Save the raw idea and original links immediately; preserve the user's wording and use a short title (usually 2–4 words, or the user's title). Do not evaluate, prioritize or develop an implementation plan unless asked.

If essential meaning is unclear, ask at most 1–2 short questions; if speed matters, use neutral placeholders instead of blocking capture. Link title/summary enrichment is optional and comes **after saving**, through the permitted web tools. Failed retrieval must not delay or undo capture; distinguish sourced facts from the user's idea.

## Note Structure

Follow the vault template `/notes/Шаблоны/Идея.md` (frontmatter `type: idea`, `status: planned`, `created` и `updated` — текущее московское время `YYYY-MM-DDTHH:mm:ss`, без кавычек; sections Идея / Зачем / Что уже известно / Что неизвестно / Следующий шаг / Связанные заметки with `[[Идеи]]`). Leave a section short or with a neutral placeholder rather than inventing content.

## Completion

Keep template sections compact; unknowns remain placeholders, not invented details. Before reporting success, confirm the core idea and original links are preserved and the section index includes the note.
