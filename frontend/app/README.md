# Company Brain — Organization Setup Wizard

Frontend-only onboarding flow that lets an administrator connect their
organization to Company Brain. The first supported identity provider is
**Google Workspace**. All network calls are mocked (see `src/services/`).

## Stack

- React 18 + TypeScript
- Vite
- Tailwind CSS (shadcn/ui-style primitives in `src/components/ui`)
- Framer Motion (page + element animations)
- Lucide icons
- Zustand (onboarding state, persisted to `localStorage`)
- React Router

## Getting started

```bash
npm install
npm run dev      # http://localhost:5173
```

Other scripts:

```bash
npm run build    # type-check + production build to dist/
npm run preview  # preview the production build
npm run lint     # tsc --noEmit
```

## Flow & routes

```
/setup              Welcome
/setup/org          Create Organization
/setup/source       Choose Identity Source
/setup/google       Connect Google Workspace (mock OAuth)   ┐ Google flow
/setup/permissions  Permissions Review                      ┘
/setup/csv          Upload CSV directory                    — CSV flow
/setup/sync         Sync Progress (~8s animated)
/setup/complete     Organization Ready
/dashboard          Dashboard placeholder
```

The wizard branches after **Choose Source**: Google Workspace goes through OAuth
+ permissions; **CSV Upload** goes through a single upload/validation step. Both
rejoin at Sync → Ready. The step indicator adapts to the active flow.

## CSV directory import

The CSV columns are derived from the Neo4j `Person` node + the org-directory
concept (`src/lib/directory.ts`):

| Column          | Required | Maps to                                   |
| --------------- | -------- | ----------------------------------------- |
| `name`          | **yes**  | `Person.name` / `canonical_name`          |
| `email`         | **yes**  | dedup key + manager linking (`Person.email`) |
| `title`         | no       | job title                                 |
| `department`    | no       | department                                |
| `manager_email` | no       | reporting hierarchy (`REPORTS_TO`)        |
| `teams`         | no       | `Person.groups` (`;` or `,` separated)    |

Headers are case-insensitive and accept common aliases (e.g. "Job Title",
"Reports To", "Org Unit"). Parsing + validation is fully client-side and real
(RFC 4180 parser in `src/lib/csv.ts`); only the upload/sync network calls are
mocked. A downloadable template is offered on the upload screen.

Validation covers: missing required columns (hard stop), empty/invalid/duplicate
emails, self-referential managers (errors), and manager emails not present in
the file (warning — the person still imports, unlinked).

## Theme — "Frozen mist"

| Token        | Hex       |
| ------------ | --------- |
| Brand/accent | `#DD700B` |
| mist-700     | `#7C7D75` |
| mist-400     | `#ADACA7` |
| mist-200     | `#D9DADF` |
| cream        | `#FCF8D8` |

Defined as CSS variables in `src/index.css` and mapped in `tailwind.config.ts`.

## Replacing the mock backend

All async I/O lives in `src/services/mockApi.ts`. Each function is documented
with a `TODO(backend)` comment describing the real endpoint it should call.
Return shapes are defined in `src/services/types.ts` and are intentionally
backend-shaped so the UI needs no changes when wiring real APIs.

### Simulating error states

The wizard handles three error kinds, each with a retry screen. Force them from
the browser console before triggering the relevant step:

```js
window.__cbMock.failOAuth = true;   // OAuth cancelled   (Connect Google)
window.__cbMock.failNetwork = true; // Network timeout   (Connect Google / CSV upload)
window.__cbMock.failSync = true;    // Sync failed       (Sync Progress)
```

## Reusable components

`PrimaryButton`, `SecondaryButton`, `ProgressBar`, `StepIndicator`,
`SourceCard`, `PermissionItem`, `SummaryCard`, `StatusBadge`, `LoadingSpinner`.
