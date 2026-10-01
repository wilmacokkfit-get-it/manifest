---
name: Supabase validation boundaries
description: Separate database and image-storage permission checks before backend changes
---

Validate Supabase database tables and Storage independently before switching the active backend. A valid backend secret and successful bucket access do not prove that the backend role has SQL table privileges.

**Why:** In this project's Supabase setup, Storage accepted the backend secret while all newly created database tables denied access. Bypassing RLS did not supply the missing table privileges.

**How to apply:** Keep the working backend selected until both services pass checks. Verify database writes and image uploads using disposable, private records and clean them up. Never treat changing backend selection as permission to migrate or delete existing local data.