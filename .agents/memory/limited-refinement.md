---
name: Limited refinement
description: Product reason for limiting follow-up context to one opportunity
---

Keep MANIFEST's generation interaction constrained: show the initial result, then offer one optional chance to add context or correct it, not a multi-turn chat.

**Why:** The creator explicitly chose to respond after seeing the first result and wanted to avoid giving users too many choices.

**How to apply:** Preserve this limit when changing the generation flow. Do not introduce repeated refinement rounds, follow-up question menus, or an ongoing conversation without a new product decision.