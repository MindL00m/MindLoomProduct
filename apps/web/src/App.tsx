import { Navigate, Route, Routes } from "react-router-dom";
import { SetupLayout } from "@/components/SetupLayout";
import { RequireSession } from "@/components/RequireSession";
import { RequireGuest } from "@/components/RequireGuest";
import { RequireAdmin } from "@/components/RequireAdmin";
import Welcome from "@/pages/setup/Welcome";
import SignIn from "@/pages/setup/SignIn";
import CreateOrg from "@/pages/setup/CreateOrg";
import UploadCsv from "@/pages/setup/UploadCsv";
import Dashboard from "@/pages/Dashboard";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/setup" replace />} />

      <Route element={<SetupLayout />}>
        <Route element={<RequireGuest />}>
          <Route path="/setup" element={<Welcome />} />
          <Route path="/setup/signin" element={<SignIn />} />
          <Route path="/setup/org" element={<CreateOrg />} />
        </Route>
        <Route element={<RequireAdmin />}>
          <Route path="/setup/csv" element={<UploadCsv />} />
        </Route>
      </Route>

      <Route element={<RequireSession />}>
        <Route path="/dashboard" element={<Dashboard />} />
      </Route>

      <Route path="*" element={<Navigate to="/setup" replace />} />
    </Routes>
  );
}
