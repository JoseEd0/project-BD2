import { useEffect, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { cancelEnrollment, finishEnrollment, startEnrollment } from "../api/faces";
import { INITIAL_STATE, enrollmentReducer, openEnrollmentId } from "../enrollment/machine";
import { rememberPerson } from "../storage/people";
import { CaptureStep } from "./enroll/CaptureStep";
import { ConsentStep } from "./enroll/ConsentStep";
import { NameStep } from "./enroll/NameStep";
import { BlockedStep, DoneStep, FinishingStep } from "./enroll/OutcomeSteps";

/** Si la persona se va a mitad del registro, la sesión del servidor se cancela y sus tomas se borran. */
function useCancelOnLeave(enrollmentId: string | null): void {
  const open = useRef(enrollmentId);
  open.current = enrollmentId;
  useEffect(() => {
    const cancel = () => {
      if (open.current !== null) {
        void cancelEnrollment(open.current);
        open.current = null;
      }
    };
    window.addEventListener("pagehide", cancel);
    return () => {
      window.removeEventListener("pagehide", cancel);
      cancel();
    };
  }, []);
}

export default function EnrollPage() {
  const [state, dispatch] = useReducer(enrollmentReducer, INITIAL_STATE);
  const [saved, setSaved] = useState(false);
  const navigate = useNavigate();
  useCancelOnLeave(openEnrollmentId(state));
  // Un pedido por estado: en desarrollo, React monta dos veces cada efecto.
  const requested = useRef<unknown>(null);

  useEffect(() => {
    if (state.step !== "starting" || requested.current === state) {
      return;
    }
    requested.current = state;
    void startEnrollment(state.name, state.consentVersion).then((outcome) =>
      dispatch(outcome.ok ? { type: "started", enrollment: outcome.data } : { type: "start-failed", problem: outcome.problem }),
    );
  }, [state]);

  const finishingId = state.step === "finishing" && state.problem === null ? state.enrollment.enrollment_id : null;
  useEffect(() => {
    if (finishingId === null || requested.current === finishingId) {
      return;
    }
    requested.current = finishingId;
    void finishEnrollment(finishingId).then((outcome) =>
      dispatch(outcome.ok ? { type: "finished", enrolled: outcome.data } : { type: "finish-failed", problem: outcome.problem }),
    );
  }, [finishingId]);

  const donePerson = state.step === "done" ? state.person : null;
  useEffect(() => {
    if (donePerson !== null) {
      setSaved(rememberPerson({ personId: donePerson.person_id, displayName: donePerson.display_name, expiresAt: donePerson.expires_at }));
    }
  }, [donePerson]);

  switch (state.step) {
    case "consent":
      return <ConsentStep notice={state.notice} onAccept={(version) => dispatch({ type: "consent-accepted", version })} />;
    case "name":
    case "starting":
      return (
        <NameStep
          initialName={state.name}
          error={state.step === "name" ? state.error : null}
          starting={state.step === "starting"}
          onSubmit={(name) => dispatch({ type: "start-requested", name })}
          onBack={() => dispatch({ type: "restart" })}
        />
      );
    case "capture":
      return (
        <CaptureStep
          enrollment={state.enrollment}
          shooting={state.shooting}
          feedback={state.feedback}
          accepted={state.accepted}
          onShotSent={() => dispatch({ type: "shot-sent" })}
          onShotAnswered={(shot) => dispatch({ type: "shot-answered", shot })}
          onShotFailed={(problem) => dispatch({ type: "shot-failed", problem })}
          onCancel={() => navigate("/")}
        />
      );
    case "finishing":
      return <FinishingStep problem={state.problem} onRetry={() => dispatch({ type: "finish-retried" })} />;
    case "done":
      return <DoneStep person={state.person} saved={saved} />;
    case "blocked":
      return <BlockedStep reason={state.reason} message={state.message} onRestart={() => dispatch({ type: "restart" })} />;
  }
}
