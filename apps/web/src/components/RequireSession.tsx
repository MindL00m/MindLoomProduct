import { Navigate, Outlet } from "react-router-dom";
import { useSession } from "@/store/session";

/** Redirect to welcome when no org session (with access token) is stored. */
export function RequireSession() {
  const isAuthenticated = useSession((s) => s.isAuthenticated);
  const accessToken = useSession((s) => s.accessToken);
  if (!isAuthenticated || !accessToken) {
    return <Navigate to="/setup" replace />;
  }
  return <Outlet />;
}
