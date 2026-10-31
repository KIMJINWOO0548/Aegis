# First-concept native WPF design QA

Historical note (2026-10-19): the pass below certifies the original Mock presentation. The user subsequently prioritized acquisition, performance and module isolation; live signal/diagnostics controls changed. That earlier visual pass does not certify every new live flow. Current functional verification is recorded in docs/DURABLE_INGESTION_REPORT.md.

Source visual truth: `docs/design-concepts/aegis-ui-1.png` (1488 × 1056 pixels).
Implementation: native WPF, initial window 980 × 720 DIP, light theme, Mock/unconnected state. Native application screenshots are the relevant evidence; this is not a browser application.

## Comparison history

1. `docs/evidence/ui/comparison-first-pass.png` places the source, proportionally normalized to 980 × 696, beside the actual 980 × 720 screenshot `desktop-first-pass.png`. Content width is matched; the source's shorter aspect ratio is preserved, without stretching. At this display, captured pixels equal window DIP (1×).
   - P2: left content measured to its children, compressing the three status groups and shortening the rule. Fix: explicit 530-DIP left content width, which also fits the minimum supported viewport.
   - P2: white 15-DIP primary text on #3388CD measured 3.79:1 contrast. Fix: base blue #2679BC, 4.62:1; darker hover/pressed colors.
   - P2: small native text appeared uneven. Display text formatting alone did not resolve the font mismatch, so this finding stayed blocked pending the next comparison.
2. `comparison-font-pass.png` / `desktop-font-pass.png`: bundled Pretendard still fell back because the absolute font-family reference was invalid. The noninteractive WPF probe reproduced all three weights failing; corrected `AppFont` to a relative resource URI. The compiled dictionary now resolves Regular, SemiBold and Bold to the embedded OTFs (`font-resolution.txt`). A temporary software-rendering experiment made no visible improvement and was reverted.
3. Final: `docs/evidence/ui/desktop-final.png`, full paired comparison `comparison-final.png`, readable status/actions crop `comparison-detail-final.png`, and pump crop `comparison-art-final.png`. Both design and actual screen are in each comparison input. Final screenshots were opened and compared after the font fix. Title/body sizes and row padding were adjusted to match the reference rhythm; earlier compressed status layout and low action contrast are resolved. No actionable P0/P1/P2 difference remains.

## Required fidelity surfaces

- Fonts/typography: Segoe UI brand/device and bundled Pretendard Korean UI; verified physical weights and native Display/ClearType rendering. Final comparison has the same readable hierarchy and unbroken copy. Native 1× hinting is sharper/more pixelated than the downsampled raster concept (accepted P3); exact source font metadata is unavailable.
- Spacing/layout: 48-DIP title bar, 35-DIP content gutter, 530-DIP left column, three status groups, two flat rows, actions and Mock notice. No clipping or horizontal overflow. Source is 24 pixels shorter after width normalization, so unused bottom space differs. Small native radius/stroke and spacing differences are accepted P3.
- Colors/tokens: cool white/gray and dark ink; accessible blue is intentionally darker than source.
- Image quality/assets: generated transparent pump artwork follows the selected industrial art direction; official Feather vectors and MIT license are bundled. Paired artwork crop confirms correct pump/motor subject, pale factory pipes, clean white blending, no visible alpha fringe, no text baked into the illustration, and adequate resolution. The regenerated pump differs in fine geometry/crop from the original concept (accepted P3).
- Copy/content: selected device from shared contract; all seven Mock/unknown-state labels retained; no claim of real equipment evaluation.

## Interaction evidence and remaining checks

Final Release solution build: zero warnings/errors, exit 0. Contract regression suite: 32 passed, zero failed/skipped, exit 0. Independent review's contrast finding is fixed; font-resource regression is independently reproduced and resolved.

Native guide opens modally (`guide-final.png`); clicking its confirmation returns focus to the main action. Escape was also exercised. Tab reaches the close action and Enter closes the main application. Caption Close exits; minimize/activate and maximize/restore work and update the accessible caption name. Initial 980×720 capture contains all content; actual 640×480 captures `desktop-minimum-final-top.png` and `desktop-minimum-final-actions.png` show that decorative artwork hides and Tab scrolls to the primary action without shrinking text. Ctrl+End reveals the footer (`desktop-minimum-final-bottom.png`). The title bar remains visible. No actual-device or API action is added.

Capture-tool limitation: the native automation tool occasionally retained dismissed transient-window images/indexes. Final visual comparisons use a fresh launch with no transient window open. Button/key outcomes were assessed from refreshed focus and window state, not dismissed-window imagery.

This is a native Windows application in the user's existing WPF project, so browser/CSS viewport and browser-console checks are inapplicable. Captured pixels equal DIP at the current 100% display scale; no OS scaling settings were changed. 150% DPI, other monitors and remote CI remain unverified. No new runtime UI errors were observed during these interactions. Python tests were not repeated for this UI-only change.

## Implementation checklist

- [x] Capture revised default window and focused comparisons.
- [x] Check minimum window and caption actions.
- [x] Complete independent review and regression checks.
- [x] Record residual limitations and accepted P3 differences.

final result: passed
