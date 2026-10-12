import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "../index.css"
import { ErrorBoundary } from "../components/ErrorBoundary"
import UnderwriterApp from "./UnderwriterApp"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary name="UnderwriterApp">
      <UnderwriterApp />
    </ErrorBoundary>
  </StrictMode>,
)
