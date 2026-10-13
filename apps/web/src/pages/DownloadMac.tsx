import { Link } from "react-router-dom";
import { DesktopAgentDownload } from "@/components/DesktopAgentDownload";

export default function DownloadMac() {
  return (
    <div className="space-y-4">
      <DesktopAgentDownload hideInstallLink />
      <p className="text-center text-sm">
        <Link
          to="/setup"
          className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          Back to sign in
        </Link>
      </p>
    </div>
  );
}
