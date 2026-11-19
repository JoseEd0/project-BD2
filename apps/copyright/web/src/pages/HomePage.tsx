import { APP } from "../identity";
import { ServiceStatus } from "../components/ServiceStatus";

export default function HomePage() {
  return (
    <>
      <title>{APP.name}</title>
      <h1>{APP.name}</h1>
      <p className="muted">{APP.description}</p>
      <ServiceStatus />
    </>
  );
}
