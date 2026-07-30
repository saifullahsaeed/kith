import { useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, ExternalLink, Loader2, Lock } from "lucide-react";

import type { ProbeOutcome, ProviderCard } from "@/lib/backend";

const inputClass =
  "w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40";

/**
 * Where the credential goes, checked as it's typed.
 *
 * The status line below the fields is the point of this step. It reports one of four
 * states, and they are genuinely different: still asking, reached-but-unauthenticated,
 * rejected, and good. A single "connected / not connected" boolean would have to lie
 * about the second — which is exactly the case a public model catalogue produces.
 */
export function ConnectStep({
  provider,
  baseUrl,
  apiKey,
  keyStored,
  probe,
  probing,
  onBaseUrl,
  onApiKey,
  onContinue,
}: {
  provider: ProviderCard;
  baseUrl: string;
  apiKey: string;
  /** A key for this same provider is already saved, so the field may be left blank. */
  keyStored: boolean;
  probe: ProbeOutcome;
  probing: boolean;
  onBaseUrl: (value: string) => void;
  onApiKey: (value: string) => void;
  onContinue: () => void;
}) {
  const first = useRef<HTMLInputElement>(null);
  // Whether the saved key is being swapped for a new one. Local, because it's about
  // what this form is showing, not about what will be saved.
  const [replacing, setReplacing] = useState(false);

  // Focus what has to be filled in first, so a pasted key needs no click.
  useEffect(() => first.current?.focus(), []);

  const needsNothing = !provider.needsKey && !provider.needsBaseUrl;

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (probe.usable) onContinue();
      }}
    >
      {provider.needsBaseUrl ? (
        <Field label="Base URL" hint="The OpenAI-compatible endpoint, ending in /v1">
          <input
            ref={first}
            className={inputClass}
            placeholder="https://integrate.api.nvidia.com/v1"
            value={baseUrl}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => onBaseUrl(event.target.value)}
          />
        </Field>
      ) : null}

      {provider.needsKey ? (
        keyStored && !replacing ? (
          // A saved key is shown as a filled, locked field rather than an empty box
          // with a hint in the placeholder. Empty-looking read as "no key here", and
          // the only way to find out otherwise was to type into it.
          <Field
            label="API key"
            hint={
              <button
                type="button"
                onClick={() => setReplacing(true)}
                className="text-kith hover:underline"
              >
                Replace it
              </button>
            }
          >
            <div className="relative">
              <input
                readOnly
                disabled
                value={"•".repeat(28)}
                className={`${inputClass} cursor-default tracking-[0.2em] opacity-70`}
                aria-label="A key is saved"
              />
              <Lock className="text-muted-foreground/50 pointer-events-none absolute top-1/2 right-3 size-3.5 -translate-y-1/2" />
            </div>
          </Field>
        ) : (
          <Field
            label={keyStored ? "New API key" : "API key"}
            hint={
              keyStored ? (
                <button
                  type="button"
                  onClick={() => {
                    // Clearing it is what makes the probe fall back to the saved key.
                    onApiKey("");
                    setReplacing(false);
                  }}
                  className="text-muted-foreground hover:text-foreground hover:underline"
                >
                  Keep the saved one
                </button>
              ) : provider.signupUrl ? (
                <a
                  href={provider.signupUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-kith inline-flex items-center gap-1 hover:underline"
                >
                  Get a key <ExternalLink className="size-3" />
                </a>
              ) : (
                "Stored on this machine, never sent anywhere else"
              )
            }
          >
            <input
              ref={provider.needsBaseUrl ? undefined : first}
              autoFocus={replacing}
              type="password"
              className={inputClass}
              placeholder="sk-…"
              value={apiKey}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => onApiKey(event.target.value)}
            />
          </Field>
        )
      ) : null}

      {needsNothing ? (
        <p className="text-muted-foreground text-sm">
          Nothing to enter — {provider.label.toLowerCase()} needs no credential. Checking whether
          it's running.
        </p>
      ) : null}

      <Status
        provider={provider}
        probe={probe}
        probing={probing}
        checkedStoredKey={keyStored && !apiKey.trim()}
      />
      {/* Submitting is what Enter does; the visible button lives in the shell's footer. */}
      <button type="submit" className="hidden" aria-hidden />
    </form>
  );
}

/** The four states, each said plainly. */
function Status({
  provider,
  probe,
  probing,
  checkedStoredKey,
}: {
  provider: ProviderCard;
  probe: ProbeOutcome;
  probing: boolean;
  /** The check that just passed used the saved key, not anything typed here. */
  checkedStoredKey: boolean;
}) {
  if (probing) {
    return (
      <Line icon={<Loader2 className="size-4 animate-spin" />} tone="muted">
        Checking…
      </Line>
    );
  }

  if (probe.usable) {
    const count = probe.models.length;
    return (
      <Line icon={<CheckCircle2 className="size-4" />} tone="good">
        {checkedStoredKey ? "Your saved key works" : "Connected"} — {count}{" "}
        {count === 1 ? "model" : "models"} available
        {probe.keyDetail ? (
          <span className="text-muted-foreground"> · {probe.keyDetail}</span>
        ) : null}
      </Line>
    );
  }

  if (probe.keyState === "rejected") {
    return (
      <Line icon={<AlertCircle className="size-4" />} tone="bad">
        {probe.keyDetail || "That key was rejected."}
      </Line>
    );
  }

  // Reached the provider but there's no key yet — worth distinguishing, because the
  // model list below is real and the only thing missing is the credential.
  if (probe.reachable && probe.keyState === "missing") {
    return (
      <Line icon={<CheckCircle2 className="size-4" />} tone="muted">
        {provider.label} is reachable — {probe.models.length} models. Paste a key to continue.
      </Line>
    );
  }

  if (probe.detail) {
    return (
      <Line icon={<AlertCircle className="size-4" />} tone="bad">
        {probe.detail}
      </Line>
    );
  }

  return <p className="text-muted-foreground/60 h-5 text-sm">{provider.requires}</p>;
}

function Line({
  icon,
  tone,
  children,
}: {
  icon: React.ReactNode;
  tone: "good" | "bad" | "muted";
  children: React.ReactNode;
}) {
  const colour =
    tone === "good" ? "text-roam" : tone === "bad" ? "text-destructive" : "text-muted-foreground";
  return (
    <p
      className={`animate-in fade-in flex min-h-5 items-start gap-2 text-sm duration-200 ${colour}`}
    >
      <span className="mt-px shrink-0">{icon}</span>
      <span>{children}</span>
    </p>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        {hint ? <span className="text-muted-foreground text-xs">{hint}</span> : null}
      </span>
      {children}
    </label>
  );
}
