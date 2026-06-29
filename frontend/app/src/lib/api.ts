/** Backend API configuration. Override at build/run time with VITE_API_BASE. */
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  "http://localhost:8000";
