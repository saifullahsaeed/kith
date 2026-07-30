import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PresenceOrb } from "@/components/presence";
import { AccessStep } from "@/components/onboarding/access-step";
import { ConnectStep } from "@/components/onboarding/connect-step";
import { ModelStep } from "@/components/onboarding/model-step";
import { ProviderChoice } from "@/components/onboarding/provider-choice";
import { ReadyStep } from "@/components/onboarding/ready-step";
import { SearchStep } from "@/components/onboarding/search-step";
import { useOnboarding } from "@/hooks/use-onboarding";
import type { ConnectionState, ProviderCard, SearchOption } from "@/lib/backend";

/** What each step says about itself. Kept together so the flow reads as a script
 *  rather than being scattered across four components. */
const COPY = {
  choose: {
    title: "Hello. I'm Kith.",
    lead: "I keep working between our conversations — reading, building, making notes. First I need somewhere to think.",
  },
  connect: { title: "Connect", lead: "" },
  model: {
    title: "Pick a model",
    lead: "This is the part of him you can change later. Start anywhere.",
  },
  search: {
    title: "How should he search?",
    lead: "He looks things up on his own. This decides who pays for that, and who can see it.",
  },
  access: {
    title: "Two things macOS decides",
    lead: "Neither can be granted from in here, so this is the one place that asks.",
  },
  ready: { title: "Ready.", lead: "Here's what he can do." },
} as const;

/**
 * First run: four steps, one decision each.
 *
 * Full-screen rather than a dialog, because there is nothing behind it to go back to —
 * and because the choice of where he thinks deserves more room than a modal gives it.
 * The step body is keyed on the step so React remounts it, which is what makes the
 * transition read as moving forward instead of content swapping in place.
 */
export function Onboarding({
  providers,
  searchOptions,
  connection,
  onEnter,
}: {
  providers: ProviderCard[];
  searchOptions: SearchOption[];
  /** What is saved right now. Non-empty when setup is being re-run rather than seen
   *  for the first time, which is what lets the key field say so. */
  connection: ConnectionState;
  onEnter: () => void;
}) {
  const flow = useOnboarding(providers, searchOptions, onEnter);
  const copy = COPY[flow.step];

  return (
    <div className="flex h-dvh flex-col bg-background text-foreground">
      {/* Draggable, and inset so the window controls have their corner. */}
      <div className="window-drag-region window-controls-gap flex h-11 shrink-0 items-center" />

      {/* Centred when it fits, scrolled when it doesn't. The inner min-h-full is what
          keeps a tall step from having its top clipped by the centring. */}
      <div className="flex-1 overflow-y-auto px-6">
        <div className="flex min-h-full items-center justify-center py-8">
          <div className="w-full max-w-2xl">
            <header className="pb-7">
              <div className="mb-5 flex items-center gap-3">
                <PresenceOrb size={12} />
                <Progress value={flow.progress} />
              </div>
              <h1 className="text-2xl font-semibold tracking-tight">
                {flow.step === "connect" && flow.provider
                  ? `Connect ${flow.provider.label}`
                  : copy.title}
              </h1>
              {(flow.step === "connect" ? flow.provider?.blurb : copy.lead) ? (
                <p className="text-muted-foreground mt-1.5 text-sm leading-relaxed">
                  {flow.step === "connect" ? flow.provider?.blurb : copy.lead}
                </p>
              ) : null}
            </header>

            <div key={flow.step} className="animate-in fade-in slide-in-from-right-3 duration-300">
              {flow.step === "choose" ? (
                <ProviderChoice providers={flow.providers} onChoose={flow.choose} />
              ) : null}

              {flow.step === "connect" && flow.provider ? (
                <ConnectStep
                  provider={flow.provider}
                  baseUrl={flow.baseUrl}
                  apiKey={flow.apiKey}
                  keyStored={connection.apiKeySet && connection.kind === flow.provider.kind}
                  probe={flow.probe}
                  probing={flow.probing}
                  onBaseUrl={flow.setBaseUrl}
                  onApiKey={flow.setApiKey}
                  onContinue={flow.toModels}
                />
              ) : null}

              {flow.step === "model" ? (
                <ModelStep
                  models={flow.probe.models}
                  suggested={flow.probe.suggested}
                  selected={flow.model}
                  onSelect={flow.chooseModel}
                />
              ) : null}

              {flow.step === "search" ? (
                <SearchStep
                  options={flow.searchOffer}
                  selected={flow.search}
                  searxUrl={flow.searxUrl}
                  onSelect={flow.setSearch}
                  onSearxUrl={flow.setSearxUrl}
                  onProbe={flow.setSearchProbe}
                />
              ) : null}

              {flow.step === "access" ? <AccessStep onDone={flow.finishAccess} /> : null}

              {flow.step === "ready" ? (
                <ReadyStep
                  providerLabel={flow.provider?.label ?? ""}
                  model={flow.model}
                  chosenModel={flow.chosenModel}
                  checks={flow.checks}
                  warnings={flow.warnings}
                />
              ) : null}
            </div>

            {flow.error ? (
              <p className="animate-in fade-in text-destructive mt-4 text-sm">{flow.error}</p>
            ) : null}

            <Footer flow={flow} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Footer({ flow }: { flow: ReturnType<typeof useOnboarding> }) {
  if (flow.step === "choose") return null;

  return (
    <div className="mt-6 flex items-center gap-3">
      {flow.step !== "ready" ? (
        <Button variant="ghost" onClick={flow.back} className="text-muted-foreground">
          <ArrowLeft className="size-4" /> Back
        </Button>
      ) : null}

      <div className="flex-1" />

      {flow.step === "connect" ? (
        <Button onClick={flow.toModels} disabled={!flow.probe.usable}>
          Continue <ArrowRight className="size-4" />
        </Button>
      ) : null}

      {flow.step === "model" ? (
        <Button onClick={flow.saveConnection} disabled={!flow.model || flow.saving}>
          {flow.saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {flow.saving ? "Saving…" : "Use this model"}
        </Button>
      ) : null}

      {flow.step === "search" ? (
        <Button onClick={flow.finish} disabled={!flow.search || flow.saving}>
          {flow.saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {/* A dead SearXNG instance is still a valid choice — it recovers — so this
              never blocks on the probe, it only reports it. */}
          {flow.saving
            ? "Saving…"
            : flow.searchProbe?.working === false
              ? "Use it anyway"
              : "Continue"}
          {!flow.saving ? <ArrowRight className="size-4" /> : null}
        </Button>
      ) : null}

      {flow.step === "ready" ? (
        <Button onClick={flow.enter} size="lg">
          Say hello <ArrowRight className="size-4" />
        </Button>
      ) : null}
    </div>
  );
}

/** A hairline rather than dots: four steps don't need counting, but knowing you're
 *  nearly done does help. */
function Progress({ value }: { value: number }) {
  return (
    <div className="bg-border/60 h-0.5 flex-1 overflow-hidden rounded-full">
      <div
        className="bg-kith h-full rounded-full transition-[width] duration-500 ease-out"
        style={{ width: `${Math.round(value * 100)}%` }}
      />
    </div>
  );
}
