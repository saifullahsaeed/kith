import { ConnectionSplash } from "@/components/connection-splash";
import { Onboarding } from "@/components/onboarding/onboarding";
import { Workspace } from "@/components/workspace";
import { ConfirmProvider } from "@/components/ui/confirm";
import { useBackendConfig } from "@/hooks/use-backend-config";
import { useSetup } from "@/hooks/use-setup";

/**
 * The states, in the order they can be answered: is the server there, is Kith set up,
 * and only then the workspace.
 *
 * Setup is checked before anything else because the workspace assumes a working
 * connection in every corner of itself. Finding out otherwise from inside it would
 * mean every panel handling a case that onboarding exists to prevent.
 */
export default function App() {
  const setup = useSetup();
  const { status, error, config, serverDefaults, updateConfig } = useBackendConfig();

  if (setup.status === "error") return <ConnectionSplash state="error" detail={setup.error} />;
  if (setup.status === "loading" || status === "loading")
    return <ConnectionSplash state="loading" />;
  if (status === "error") return <ConnectionSplash state="error" detail={error} />;

  if (setup.status === "onboarding" && setup.snapshot) {
    return (
      <Onboarding
        providers={setup.snapshot.providers}
        searchOptions={setup.snapshot.search.options}
        connection={setup.snapshot.connection}
        onEnter={setup.enter}
      />
    );
  }

  return (
    // Every "are you sure?" in the app goes through this instead of window.confirm.
    <ConfirmProvider>
      <Workspace config={config} serverDefaults={serverDefaults} onSaveConfig={updateConfig} />
    </ConfirmProvider>
  );
}
