import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App.tsx";
import { ErrorBoundary } from "./components/shell/error-boundary";
import { installApiToken } from "./lib/api-token";
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
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
);
