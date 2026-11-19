import { Link } from "react-router";

import { TextScreen } from "../components/TextScreen";

export function NotFoundPage() {
  return (
    <TextScreen
      title="Esta dirección no existe"
      back={{ to: "/", label: "Ir al inicio" }}
      actions={
        <Link className="button button-primary button-block" to="/">
          Ir al inicio
        </Link>
      }
    >
      <p className="lead">Quizás el enlace está incompleto. Desde el inicio puedes registrarte o probar el reconocimiento.</p>
    </TextScreen>
  );
}
