---
name: ideas-inbox
description: "Use when the owner says «запиши идею/мысль» — quick capture to /notes/Идеи."
version: 2.1.0
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

Destination: `/notes/Идеи/<Короткое название>.md`, then add one line `- [[<Название>]] — <суть>` to the matching section of `/notes/Идеи/Идеи.md`.

Vault mechanics (paths, file tools, wikilinks) — see the `obsidian` skill. Note template — `/notes/Шаблоны/Идея.md`.

## When to Use

Use when the user says things like:
- "запиши идею"
- "сохрани мысль"
- "закинь в идеи"
- "потом к этому вернёмся"
- "вот мысль, чтобы не потерять"

Do not use for:
- song ideas, if the owner has a dedicated songwriting skill — follow that skill instead
- full project planning
- deep technical design
- task tracking / TODO lists
- research summaries

## Core Behavior

1. Capture the user's raw idea without over-interpreting it.
2. Create a short title, usually 2-4 words.
3. Preserve the main point in plain language.
4. Add only light structure so the idea is easy to revisit later.
5. Avoid expanding into implementation details unless the user asks.
6. If the idea is already understandable, save it immediately without slowing the user down.
7. If critical meaning is unclear, ask at most 1-2 short clarification questions.
8. If the user does not answer or wants speed, save the note with neutral placeholders rather than blocking capture.

## Note Structure

Follow the vault template `/notes/Шаблоны/Идея.md` (frontmatter `type: idea`, `status: planned`, `created` и `updated` — текущее московское время `YYYY-MM-DDTHH:mm:ss`, без кавычек; sections Идея / Зачем / Что уже известно / Что неизвестно / Следующий шаг / Связанные заметки with `[[Идеи]]`). Leave a section short or with a neutral placeholder rather than inventing content.

## Writing Rules

- Keep the note compact.
- Preserve the user's wording where it carries the core meaning.
- If parts are unclear, prefer a neutral placeholder over invention.
- If the idea is very raw, keep `Что уточнить позже` short and honest.
- If the user already gave a good title, reuse it.
- If the user did not ask for analysis, do not score, critique, or prioritize the idea.
- A minimal but acceptable capture is: short title + `## Идея` + 1-2 bullets under either usefulness or open questions.
- Prefer saving a slightly rough note now over delaying capture for polish.

## Naming

By default:
- make the filename short
- prefer 2-4 words
- avoid long subtitles
- avoid dates in the title unless the user wants them

## Common Pitfalls

1. Turning a rough idea into a full plan too early.
2. Adding technical details the user did not ask for.
3. Making the note so short that the original meaning is lost.
4. Making the note so long that it stops being an inbox capture.

## Verification Checklist

- [ ] The title is short and readable.
- [ ] The core idea is preserved.
- [ ] The note is compact.
- [ ] Open questions are separated from the main idea.
- [ ] No unnecessary implementation detail was added.
