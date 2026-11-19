import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { UpdatePrompt } from "./components/UpdatePrompt";
import { CustomerProvider } from "./customer/CustomerContext";
import { LocationProvider } from "./location/LocationContext";
import { PhotoProvider } from "./photo/PhotoContext";
import { routes } from "./routes";

/** Un fallo con motivo (un 400, un 404) no se arregla repitiendo; uno de red, quizá una vez. */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failures, error) => failures < 1 && (error as { retryable?: boolean }).retryable === true,
      refetchOnWindowFocus: false,
    },
  },
});
const router = createBrowserRouter(routes);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ShopProviders>
        <RouterProvider router={router} />
      </ShopProviders>
      <UpdatePrompt />
    </QueryClientProvider>
  );
}

export function ShopProviders({ children }: { readonly children: React.ReactNode }) {
  return (
    <CustomerProvider>
      <LocationProvider>
        <PhotoProvider>{children}</PhotoProvider>
      </LocationProvider>
    </CustomerProvider>
  );
}
