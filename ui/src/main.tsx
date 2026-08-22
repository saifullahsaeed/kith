import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import App from "./App.tsx";
import { ErrorBoundary } from "./components/shell/error-boundary";
import { installApiToken } from "./lib/api-token";
import { queryClient } from "./lib/query";
import { surfaceFailures } from "./lib/surface-failures";

// Before anything renders: the server rejects API calls without the shared secret, and the
// very first thing App does is fetch its config.
installApiToken();

// And before that: a rejection nothing caught used to leave no trace anywhere.
surfaceFailures();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Outermost, so an error React cannot recover from is a message with a way back rather
        than the white window it used to be. */}
    <ErrorBoundary where="Kith">
      {/* One cache for everything the server knows, above everything that reads it. What keeps it
          honest is `useLiveUpdates` in App — see lib/query.ts for why the defaults are what they
          are, and hooks/use-live.ts for the stream that invalidates them. */}
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
