import { Navigate, Route, Routes } from "react-router-dom";
import { SetupLayout } from "@/components/SetupLayout";
import Welcome from "@/pages/setup/Welcome";
import CreateOrg from "@/pages/setup/CreateOrg";
import ChooseSource from "@/pages/setup/ChooseSource";
import ConnectGoogle from "@/pages/setup/ConnectGoogle";
import UploadCsv from "@/pages/setup/UploadCsv";
import Permissions from "@/pages/setup/Permissions";
import SyncProgress from "@/pages/setup/SyncProgress";
import Complete from "@/pages/setup/Complete";
import Dashboard from "@/pages/Dashboard";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/setup" replace />} />

      <Route element={<SetupLayout />}>
        <Route path="/setup" element={<Welcome />} />
        <Route path="/setup/org" element={<CreateOrg />} />
        <Route path="/setup/source" element={<ChooseSource />} />
        <Route path="/setup/google" element={<ConnectGoogle />} />
        <Route path="/setup/csv" element={<UploadCsv />} />
        <Route path="/setup/permissions" element={<Permissions />} />
        <Route path="/setup/sync" element={<SyncProgress />} />
        <Route path="/setup/complete" element={<Complete />} />
      </Route>

      <Route path="/dashboard" element={<Dashboard />} />

      <Route path="*" element={<Navigate to="/setup" replace />} />
    </Routes>
  );
}
