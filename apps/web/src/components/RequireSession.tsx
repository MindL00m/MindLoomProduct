import { Navigate, Outlet } from "react-router-dom";
import { useSession } from "@/store/session";

/** Redirect to welcome when no org session is stored. */
export function RequireSession() {
  const isAuthenticated = useSession((s) => s.isAuthenticated);
  if (!isAuthenticated) {
    return <Navigate to="/setup" replace />;
  }
  return <Outlet />;
}
