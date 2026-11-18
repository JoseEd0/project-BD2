import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { UpdatePrompt } from "./components/UpdatePrompt";
import { routes } from "./routes";

const queryClient = new QueryClient();
const router = createBrowserRouter(routes);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <UpdatePrompt />
    </QueryClientProvider>
  );
}
