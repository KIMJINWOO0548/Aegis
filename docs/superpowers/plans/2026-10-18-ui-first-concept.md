# Selected UI implementation

User choice: first visual, `docs/design-concepts/aegis-ui-1.png`. Keep the existing native WPF application and D0a Mock boundary.

1. Use the selected light layout, Korean type hierarchy, equipment illustration and official Feather icons. Reuse the contract device ID.
2. Implement the connection guide as an owner dialog. Preserve window caption actions, resizing, keyboard focus and scrolling at the minimum size.
3. Build the solution, run the existing contract regressions, obtain an independent code review, and inspect the running native application.
4. Compare actual screenshots with the source at matched density. Fix substantive differences and record evidence and limitations in `design-qa.md`.

No actual equipment connection, token collection, analysis, warning acknowledgement, installation or publishing is part of this UI change.
