# Loom Web

Main Loom web application. It includes organization onboarding, the dashboard,
AI question answering, uploads, organization and knowledge graphs, and connected
apps. Some onboarding operations retain development-mode fallbacks, while the
main application calls the Loom API in `apps/api`.

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
/setup/csv          Optional admin-only employee directory import
/dashboard          Main app (requires session)
```

Creating an organization goes directly to the dashboard. Administrators then
connect company knowledge from **Apps**, optionally add an employee directory
from **Organization**, and invite employees later. Members do not see workspace
connection or organization-creation controls.
