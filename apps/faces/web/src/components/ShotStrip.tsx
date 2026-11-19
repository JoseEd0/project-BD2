import type { Pose } from "../api/faces";
import { Icon, type IconName } from "./Icon";

const POSE_NAMES: Record<Pose, string> = { front: "De frente", left: "Izquierda", right: "Derecha" };
const POSE_ICONS: Record<Pose, IconName> = { front: "faceFront", left: "faceLeft", right: "faceRight" };

export function poseName(pose: Pose): string {
  return POSE_NAMES[pose];
}

export interface ShotStripProps {
  readonly poses: readonly Pose[];
  readonly covered: readonly Pose[];
  readonly current: Pose | null;
}

/** La tira de la cabina: cada toma del plan en un cuadro del mismo tamaño; se llena al aceptarse. */
export function ShotStrip({ poses, covered, current }: ShotStripProps) {
  return (
    <ol className="strip" aria-label={`Tomas: ${covered.length} de ${poses.length}`}>
      {poses.map((pose) => {
        const done = covered.includes(pose);
        const state = done ? "done" : pose === current ? "current" : "pending";
        const status = done ? "lista" : pose === current ? "ahora" : "pendiente";
        return (
          <li key={pose} className="strip-frame" data-state={state} aria-label={`${POSE_NAMES[pose]}: ${status}`}>
            <Icon name={done ? "check" : POSE_ICONS[pose]} />
            <span aria-hidden="true">{POSE_NAMES[pose]}</span>
          </li>
        );
      })}
    </ol>
  );
}
