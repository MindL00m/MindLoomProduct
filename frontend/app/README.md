# Loom — Organization Setup Wizard

Frontend-only onboarding flow that lets an administrator connect their
organization to Loom. The first supported identity provider is
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
/setup/signin       Sign in (returning users)
/setup/org          Create Organization
/setup/source       Choose Identity Source
/setup/google       Connect Google Workspace (mock OAuth)   ┐ Google flow
/setup/permissions  Permissions Review                      ┘
/setup/csv          Upload CSV directory                    — CSV flow
/setup/sync         Sync progress
/setup/complete     Done
/dashboard          Main app (requires session)
```

The wizard branches after **Choose Source**: Google Workspace goes through OAuth
and permissions; CSV goes straight to upload. Both rejoin at Sync → Ready.

The step timeline at the top is shown **only** on the create-organization path
(not on Welcome or Sign-in).

## Mock controls

In the browser console during development:

```js
window.__cbMock.failOAuth = true;   // OAuth cancelled   (Connect Google)
window.__cbMock.failNetwork = true; // Network timeout   (Connect Google / CSV upload)
```
