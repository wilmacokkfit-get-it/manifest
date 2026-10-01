---
name: Python hook environment
description: Replit Python dependency installation in non-interactive merge hooks
---

Do not assume that a project-local Python launcher makes ordinary pip installation or the sysconfig installation paths writable. In this environment, they can still resolve to immutable Nix system paths.

**Why:** A non-interactive merge hook failed with an externally managed environment error despite Python loading project-local packages.

**How to apply:** Use the package-management tools for interactive dependency changes. For repository hooks that must install dependencies, target the project-local package directory explicitly, derive its Python version from the interpreter, and do not attempt to modify the Nix system installation or create a separate virtual environment.