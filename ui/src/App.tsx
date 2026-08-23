import { ConnectionSplash } from "@/components/shell/connection-splash";
import { ContextMenu } from "@/components/shell/context-menu";
import { Onboarding } from "@/components/onboarding/onboarding";
import { Workspace } from "@/components/shell/workspace";
import { ConfirmProvider } from "@/components/ui/confirm";
import { PromptProvider } from "@/components/ui/prompt";
import { useBackendConfig } from "@/hooks/use-backend-config";
import { useLiveUpdates } from "@/hooks/use-live";
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
  const { status, error, config, updateConfig, reload } = useBackendConfig();

  /* The one subscription, above every screen and outside every branch below.
   *
   * Here rather than in the workspace on purpose: it must not be torn down and rebuilt as the app
   * moves between the splash, onboarding and the workspace. A stream that reconnects whenever the
   * top-level view changes is a stream with a gap at exactly the moment something is happening. */
  useLiveUpdates();

  if (setup.status === "error") return <ConnectionSplash state="error" detail={setup.error} />;
  if (setup.status === "loading" || status === "loading")
    return <ConnectionSplash state="loading" />;
  if (status === "error") return <ConnectionSplash state="error" detail={error} />;

  if (setup.status === "onboarding" && setup.snapshot) {
    return (
      <>
        <ContextMenu />
        <Onboarding
          providers={setup.snapshot.providers}
          searchOptions={setup.snapshot.search.options}
          connection={setup.snapshot.connection}
          onEnter={setup.enter}
        />
      </>
    );
  }

  return (
    // Every "are you sure?" in the app goes through this instead of window.confirm.
    <ConfirmProvider>
      {/* The other half of what `window.confirm`/`window.prompt` used to do. Electron implements
          the first and throws on the second, which is why asking for a name needs a real dialog. */}
      <PromptProvider>
        <ContextMenu />
        <Workspace config={config} onSaveConfig={updateConfig} onConnectionSaved={reload} />
      </PromptProvider>
    </ConfirmProvider>
  );
}
