import { Link } from "react-router";

import { Icon, type IconName } from "../components/Icon";
import { ServiceLine } from "../components/ServiceLine";
import { TextScreen } from "../components/TextScreen";
import { loadPeople } from "../storage/people";

interface ChoiceProps {
  readonly to: string;
  readonly icon: IconName;
  readonly title: string;
  readonly detail: string;
}

function Choice({ to, icon, title, detail }: ChoiceProps) {
  return (
    <li>
      <Link to={to} className="home-choice">
        <span className="home-choice-icon">
          <Icon name={icon} />
        </span>
        <span className="home-choice-text">
          <strong>{title}</strong>
          <span>{detail}</span>
        </span>
        <Icon name="chevron" />
      </Link>
    </li>
  );
}

function myDataDetail(): string {
  const registered = loadPeople();
  if (registered.length === 0) {
    return "Mira qué guardamos de ti y bórralo con tu código.";
  }
  return `Ver o borrar el registro de ${registered.map((person) => person.displayName).join(", ")}.`;
}

export default function HomePage() {
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
      <nav className="home-nav" aria-label="Qué quieres hacer">
        <Link to="/registro" className="home-start">
          <span className="home-start-text">
            <strong>Registrarme</strong>
            <span>Tres fotos de tu cara para que la cámara aprenda a reconocerte. Toma un minuto.</span>
          </span>
          <span className="home-start-go" aria-hidden="true">
            <Icon name="arrowRight" />
          </span>
        </Link>
        <ul className="home-list">
          <Choice to="/reconocer" icon="scan" title="Reconocer en vivo" detail="Apunta la cámara a alguien: si se registró, ves su nombre al instante." />
          <Choice to="/famosos" icon="faceFront" title="¿A qué famoso te pareces?" detail="Una selfie contra 5 740 personas públicas. No hace falta registrarte." />
          <Choice to="/mis-datos" icon="shield" title="Mis datos" detail={myDataDetail()} />
        </ul>
      </nav>
      <ServiceLine />
    </TextScreen>
  );
}
