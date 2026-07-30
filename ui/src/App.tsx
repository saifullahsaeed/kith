import { ConnectionSplash } from "@/components/connection-splash";
import { Workspace } from "@/components/workspace";
import { ConfirmProvider } from "@/components/ui/confirm";
import { useBackendConfig } from "@/hooks/use-backend-config";

export default function App() {
  const { status, error, config, serverDefaults, updateConfig } = useBackendConfig();

  if (status === "loading") return <ConnectionSplash state="loading" />;
  if (status === "error") return <ConnectionSplash state="error" detail={error} />;

  return (
    // Every "are you sure?" in the app goes through this instead of window.confirm.
    <ConfirmProvider>
      <Workspace config={config} serverDefaults={serverDefaults} onSaveConfig={updateConfig} />
    </ConfirmProvider>
  );
}
