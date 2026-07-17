import { Navigate, Outlet } from "react-router-dom";
import { useSession } from "@/store/session";

/** Frontend convenience guard; the API independently enforces admin access. */
export function RequireAdmin() {
  const authenticated = useSession((state) => state.isAuthenticated);
  const role = useSession((state) => state.role);
  if (!authenticated || role !== "admin") {
    return <Navigate to={authenticated ? "/dashboard" : "/setup"} replace />;
  }
  return <Outlet />;
}
