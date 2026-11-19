import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

const STORAGE_KEY = "cajon.cliente";

interface CustomerState {
  /** El cliente para quien se recomienda y quien compra, o `null` si no se eligió. */
  readonly customer: string | null;
  readonly chooseCustomer: (customer: string | null) => void;
}

const CustomerContext = createContext<CustomerState | null>(null);

function remembered(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function remember(customer: string | null): void {
  try {
    if (customer === null) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, customer);
    }
  } catch {
    // Sin almacenamiento (ventana privada), el cliente vale solo hasta recargar.
  }
}

/** El cliente elegido, recordado entre visitas en este navegador. */
export function CustomerProvider({ children }: { readonly children: ReactNode }) {
  const [customer, setCustomer] = useState<string | null>(remembered);
  const chooseCustomer = useCallback((next: string | null) => {
    remember(next);
    setCustomer(next);
  }, []);
  const value = useMemo(() => ({ customer, chooseCustomer }), [customer, chooseCustomer]);
  return <CustomerContext.Provider value={value}>{children}</CustomerContext.Provider>;
}

export function useCustomer(): CustomerState {
  const state = useContext(CustomerContext);
  if (state === null) {
    throw new Error("useCustomer necesita un CustomerProvider");
  }
  return state;
}
