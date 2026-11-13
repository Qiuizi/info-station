import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";

import { TooltipProvider } from "@/components/ui/tooltip";
import { initTheme } from "@/hooks/use-theme";

import App from "./App";
import "./index.css";

initTheme();
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: true } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <App />
        <Toaster position="bottom-right" toastOptions={{ className: "!rounded-2xl !border-0 !shadow-pop" }} />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
);
