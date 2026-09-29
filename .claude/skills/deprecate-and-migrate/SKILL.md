---
name: deprecate-and-migrate
description: Change or remove public API, CLI flags or stored formats without breaking users - deprecation warnings, a compatibility period, migrations. Use when a change renames, removes or changes the meaning of anything users or their saved data depend on.
---

# Deprecate and migrate

Users and their saved data outlive any version. Break nothing silently.

**Public API and CLI**
1. Keep the old name working for at least one minor release: it calls the new one and warns with
   `warnings.warn("<old> is deprecated, use <new>; it will be removed in <version>", DeprecationWarning, stacklevel=2)`.
   CLI: the same message on stderr.
2. Docs and examples show only the new form. `CHANGELOG.md`: **Deprecated** now, **Removed** when it goes.
3. Tests: the old form still works and warns; the new form works.

**Workspaces and the records it reads** (`docs/sources.md`, `design/current.md` §5)
1. A workspace folder outlives versions. New optional data is fine; anything else is a change record
   with a "Migration and compatibility" section.
2. An old workspace still opens, or is refused with a `HoneLensError` that says how to migrate. Never
   guess. The same goes for older record formats a source reads.
3. Keep a small old workspace or record file in the tests and test opening it and the refusal.
