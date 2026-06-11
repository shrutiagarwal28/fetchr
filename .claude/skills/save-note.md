---
description: Save an insight, interview angle, concept, or decision from the current conversation to notes/personal-notes.md. Invoke any time something is worth keeping — interview framing, architectural patterns, watch-out-fors, design decisions.
---

Look at the most recent exchange in this conversation and identify what the user wants to save. If they passed an argument (e.g. `/save-note the FK explanation`), use that to pinpoint the specific content. Otherwise use the last discussed topic.

Append a new entry to `/Users/shruti/IdeaProjects/fetchr/notes/personal-notes.md` using this format:

```
--------------------------------

### <Topic — 6 words or fewer>

**Category:** <choose one: Interview Angle | Pattern | Concept | Decision | Watch Out For | Architecture>

**Date:** YYYY-MM-DD

<The note body — 3–10 sentences. Prioritise the "why" and how to talk about it in an interview or design discussion. Include any specific phrasing worth reusing.>
```

Rules:
- Always append — never overwrite or remove existing content
- Keep the body concise but complete enough to be useful cold, without re-reading the full conversation
- If the content has an interview angle, lead with how you'd phrase it to an interviewer
- Create the file if it doesn't exist
