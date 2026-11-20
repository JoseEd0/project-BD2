import { Link } from "react-router";

import { Icon, type IconName } from "../components/Icon";
import { ServiceLine } from "../components/ServiceLine";
import { TextScreen } from "../components/TextScreen";
import { ThemeChoice } from "../components/ThemeChoice";
import { loadPeople } from "../storage/people";

function Choice({ to, icon, title, detail, primary = false }: { to: string; icon: IconName; title: string; detail: string; primary?: boolean }) {
  return (
    <Link to={to} className={primary ? "home-choice home-choice-primary" : "home-choice"}>
      <span className="home-choice-icon">
        <Icon name={icon} />
      </span>
      <span>
        <strong>{title}</strong>
        <span>{detail}</span>
      </span>
      <Icon name="chevron" />
    </Link>
  );
}

export default function HomePage() {
  const registered = loadPeople();
  return (
    <TextScreen
      title="Tu cara, tres tomas y listo"
      documentTitle="Rostros"
      intro={
        <span className="home-hero">
          <span>Regístrate con tu celular y mira cómo la cámara te reconoce en vivo. No guardamos fotos.</span>
          <span className="home-strip" aria-hidden="true">
            <span>
              <Icon name="faceFront" />
            </span>
            <span>
              <Icon name="faceLeft" />
            </span>
            <span>
              <Icon name="faceRight" />
            </span>
          </span>
        </span>
      }
    >
      <nav className="home-actions" aria-label="Qué quieres hacer">
        <Choice to="/registro" icon="user" title="Registrarme" detail="Consentimiento, tu nombre y tres tomas" primary />
        <Choice to="/reconocer" icon="scan" title="Reconocer en vivo" detail="Apunta la cámara a una o varias caras" />
        <Choice to="/famosos" icon="faceFront" title="¿A qué famoso te pareces?" detail="Una selfie contra 5 749 personas públicas" />
        <Choice
          to="/mis-datos"
          icon="shield"
          title="Mis datos"
          detail={registered.length > 0 ? `Ver o borrar ${registered.map((person) => person.displayName).join(", ")}` : "Ver o borrar un registro con tu código"}
        />
      </nav>
      <ServiceLine />
      <ThemeChoice />
    </TextScreen>
  );
}
