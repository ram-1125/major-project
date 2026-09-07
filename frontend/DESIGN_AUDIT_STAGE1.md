# SmartOps UI Redesign Stage 1 — pre-implementation audit

Date: 2026-09-02

This audit was recorded before the Stage 1 shell and design-system styles were
changed. The external visual reference was unavailable from the development
environment, so the owner-provided requirements are the authoritative design
specification. No reference branding, assets, code, text, or layout were used.

## Production and behavior baseline

- The implementation is frontend-only. No schema or API change is required.
- Production schema is 18. Baseline v2 is active, v1 is archived for rollback,
  no candidate exists, and no recalibration is collecting.
- All eight hash routes, the 30-profile PC Quality selector, optional confirmed
  recalibration, pipeline state, alert evidence, notification settings, fixed
  shell, route-scoped refresh, charts, and technical disclosures must remain.
- Before screenshots were captured at 1366×768 and 1920×1080 using the local
  API and Vite only; the telemetry agent was not started.

## Existing strengths to preserve

- Semantic hash navigation, skip link, `aria-current`, fixed header/sidebar,
  one scroll-owning main region, responsive drawer and reduced-motion rules.
- Classic light enterprise surfaces, status text paired with status colour,
  safe focus-visible outlines, table wrappers and explicit loading/error/empty
  states.
- Existing system-font stack; no remote font or asset dependency.
- Reusable shell, metric-card, section-heading, status, form and table classes.

## Findings

1. `styles.css` contains 4,312 lines and many repeated literal greys, blues,
   borders, radii and shadows. This makes hierarchy and future maintenance
   inconsistent. Central design tokens are needed before visual refinement.
2. The top bar combines branding and a long connection sentence, but does not
   identify the current page or provide compact notification/settings/search
   access. Controls crowd at laptop and mobile widths.
3. The sidebar uses two-letter text boxes instead of a coherent icon system.
   It has one broad Workspace group, a weak white selected state, no desktop
   collapse preference, and no feature navigation search.
4. Cards depend heavily on borders and small uppercase labels. Numerous
   surfaces have nearly identical weight, producing a flat hierarchy and dense
   technical appearance.
5. Buttons, selects, badges, disclosures, loading states and alerts use several
   slightly different borders/radii and do not consistently reserve a 40–44px
   interaction target.
6. Status colours are generally semantically correct, but literal values vary
   between pages. Tokens must preserve green/blue/amber/orange/red meanings and
   must never remove accompanying text or icons.
7. Existing desktop fixed-shell behavior is sound and should be extended, not
   replaced. Mobile drawer behavior is also reusable, but needs stronger focus,
   close, search and viewport containment rules.
8. Existing charts are functionally complete and responsive. Stage 1 should
   only apply shared surface/control tokens; chart geometry and data behavior
   remain out of scope.
9. No icon package is installed. A single lightweight local React icon library
   is justified to remove text/emoji substitutes and provide consistent,
   accessible SVG icons.
10. Before browser checks found no route failure or root horizontal overflow at
    the two desktop audit sizes. Smaller widths require dedicated regression
    tests after the new collapse/search/top-bar behavior is added.

## Stage 1 implementation decisions

- Add an original SmartOps light design system through CSS custom properties.
- Use one locally bundled open-source outline icon family.
- Keep the existing DOM/data ownership where practical; refactor only the
  application shell and shared presentation helpers.
- Build search from a version-controlled catalogue of actual SmartOps routes,
  Settings destinations, major features and stable PC Quality profile IDs.
- Persist only the desktop sidebar presentation preference in `localStorage`.
- Defer dark theme and chart redesign so neither is partially implemented.

