import { Navigate, Outlet } from "react-router-dom";
import { useSession } from "@/store/session";

/** Keep signed-in users out of organization creation and sign-in screens. */
export function RequireGuest() {
  const authenticated = useSession((state) => state.isAuthenticated);
  return authenticated ? <Navigate to="/dashboard" replace /> : <Outlet />;
}
