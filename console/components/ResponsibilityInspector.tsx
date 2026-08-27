import type {
  ResponsibilityAuthorization,
  ResponsibilityInspector as ResponsibilityInspectorModel,
} from "@/lib/responsibility-types";
import { formatTime, jsonText, shortId } from "@/lib/format";

function State({ value }: { value: boolean | null | undefined }) {
  if (value === null || value === undefined) return <span>n/a</span>;
  return <strong>{value ? "yes" : "no"}</strong>;
}

function Scope({ scope }: { scope: Record<string, unknown> }) {
  const entries = Object.entries(scope);
  if (entries.length === 0) return <span>—</span>;
  return (
    <div className="kv-grid">
      {entries.map(([key, value]) => (
        <div key={key}>
          <span className="detail-label">{key}</span>{" "}
          <span className="mono">{typeof value === "string" ? value : jsonText(value)}</span>
        </div>
      ))}
    </div>
  );
}

function AuthorizationChain({
  authorization,
  responsibility,
}: {
  authorization: ResponsibilityAuthorization;
  responsibility: ResponsibilityInspectorModel;
}) {
  const decision = responsibility.decisions.find(
    (candidate) => candidate.id === authorization.decisionRef,
  );
  const effects = responsibility.execution.filter(
    (effect) => effect.authorizationRef === authorization.id,
  );

  return (
    <div className="work-item">
      <div className="work-item-head">
        <strong>{decision?.authorizationProfile ?? "execution-authorization"}</strong>
        <span className="mono">{shortId(authorization.id)}</span>
      </div>

      <div className="work-item-meta">
        <span className="detail-label">Decision</span>
        <span className="mono">{decision ? shortId(decision.id) : authorization.decisionRef}</span>
        <span className="detail-label">责任位置</span>
        <span>{decision?.nextStep ?? "—"}</span>
        <span className="detail-label">角色</span>
        <span>{decision?.requiredRoles.join(", ") || "—"}</span>
        <span className="detail-label">目标系统</span>
        <span>{authorization.targetSystem}</span>
      </div>

      <div>
        <span className="detail-label">Subject</span>{" "}
        <span className="mono">
          {authorization.subjectType}:{authorization.subjectRef}@{authorization.subjectVersion ?? "—"}
        </span>
      </div>

      <div>
        <span className="detail-label">Allowed operations</span>
        <ul>
          {authorization.allowedOperations.map((operation) => (
            <li key={operation} className="mono">
              {operation}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <span className="detail-label">Approved scope</span>
        <Scope scope={authorization.scope} />
      </div>

      <div>
        <span className="detail-label">Issued</span>{" "}
        <span>{formatTime(authorization.issuedAt)}</span>{" "}
        <span className="detail-label">by</span>{" "}
        <span className="mono">{shortId(authorization.issuedByUserId)}</span>
      </div>

      <div>
        <span className="detail-label">Effects bound by exact authorizationRef</span>
        {effects.length === 0 ? (
          <p className="empty">尚无绑定 effect。</p>
        ) : (
          <ol className="timeline">
            {effects.map((effect) => {
              const reality = responsibility.reality.filter(
                (assessment) => assessment.effectId === effect.effectId,
              );
              const outcomes = responsibility.confirmedOutcomes.filter(
                (outcome) => outcome.effectId === effect.effectId,
              );
              return (
                <li key={effect.effectId} className="timeline-item">
                  <div className="timeline-head">
                    <span className="timeline-type">{effect.operation}</span>
                    <span>{effect.status}</span>
                  </div>
                  <div>
                    Effect <span className="mono">{shortId(effect.effectId)}</span>
                    {reality.map((assessment) => (
                      <div key={assessment.assessmentId}>
                        Reality → <span className="mono">{shortId(assessment.assessmentId)}</span>{" "}
                        ({assessment.status})
                      </div>
                    ))}
                    {outcomes.map((outcome) => (
                      <div key={outcome.id}>
                        Outcome → <span className="mono">{shortId(outcome.id)}</span>{" "}
                        ({outcome.outcomeType}) · realization{" "}
                        <span className="mono">{shortId(outcome.realizationAssessmentRef)}</span>
                      </div>
                    ))}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}

function CurrentVsHistorical({
  responsibility,
}: {
  responsibility: ResponsibilityInspectorModel;
}) {
  const current = responsibility.currentResponsibility;
  return (
    <div className="card">
      <h2>CURRENT vs HISTORICAL</h2>
      <div className="kv-grid">
        <div>
          <h3>CURRENT</h3>
          {current ? (
            <div className="work-item">
              <div>
                <span className="detail-label">current use eligible</span>{" "}
                <State value={current.eligible} /> · {current.status}
              </div>
              <div>
                <span className="detail-label">authorization current</span>{" "}
                <State value={current.authorizationCurrent} />
              </div>
              <div>
                <span className="detail-label">publication qualification current</span>{" "}
                <State value={current.publicationQualificationCurrent} />
              </div>
              <div>
                <span className="detail-label">portable status</span>{" "}
                <span>{current.portableStatus}</span>
              </div>
              <div>
                <span className="detail-label">applicable open obligations</span>{" "}
                <span>{current.applicableObligationRefs.length}</span>
              </div>
              {current.reasons.length > 0 ? (
                <ul>
                  {current.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : (
            <p className="empty">该 workflow 没有 listing CURRENT eligibility projection。</p>
          )}
        </div>

        <div>
          <h3>HISTORICAL</h3>
          {responsibility.historical.length === 0 ? (
            <p className="empty">没有 HistoricalExperienceUse binding。</p>
          ) : (
            <div className="work-items">
              {responsibility.historical.map((historical) => (
                <div key={historical.historicalUseRef} className="work-item">
                  <div>
                    <span className="detail-label">Historical use</span>{" "}
                    <span className="mono">{historical.historicalUseRef}</span>
                  </div>
                  <div>
                    <span className="detail-label">Judgment</span>{" "}
                    <span className="mono">{historical.judgmentRef}</span>
                  </div>
                  <div>
                    <span className="detail-label">Subject version</span>{" "}
                    <span className="mono">{historical.subjectVersion}</span>
                  </div>
                  <div>
                    <span className="detail-label">Requirement digest</span>{" "}
                    <span className="mono">{historical.requirementDigest}</span>
                  </div>
                  <div>
                    <span className="detail-label">Snapshot digest</span>{" "}
                    <span className="mono">{historical.snapshotDigest}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <p className="muted">
        Historical use 记录当时实际依赖的知识状态；CURRENT 由服务器重新评估，二者不会相互改写。
      </p>
    </div>
  );
}

function ExecutionRealityOutcome({
  responsibility,
}: {
  responsibility: ResponsibilityInspectorModel;
}) {
  return (
    <div className="card">
      <h2>Execution vs Reality vs Business Outcome</h2>
      <p className="muted">
        EffectLedger execution report、EffectRealization verification 与 ConfirmedOutcome 是三个独立事实。
      </p>
      {responsibility.execution.length === 0 ? (
        <p className="empty">暂无 effect。</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Effect</th>
                <th>Execution report</th>
                <th>Reality verification</th>
                <th>Business outcome</th>
              </tr>
            </thead>
            <tbody>
              {responsibility.execution.map((effect) => {
                const reality = responsibility.reality.filter(
                  (assessment) => assessment.effectId === effect.effectId,
                );
                const outcomes = responsibility.confirmedOutcomes.filter(
                  (outcome) => outcome.effectId === effect.effectId,
                );
                return (
                  <tr key={effect.effectId}>
                    <td>
                      <div className="mono">{effect.operation}</div>
                      <div className="mono">{shortId(effect.effectId)}</div>
                    </td>
                    <td>{effect.status}</td>
                    <td>
                      {reality.length === 0
                        ? "—"
                        : reality.map((assessment) => (
                            <div key={assessment.assessmentId}>
                              {assessment.status} ·{" "}
                              <span className="mono">{shortId(assessment.assessmentId)}</span>
                            </div>
                          ))}
                    </td>
                    <td>
                      {outcomes.length === 0
                        ? "—"
                        : outcomes.map((outcome) => (
                            <div key={outcome.id}>
                              {outcome.outcomeType} ·{" "}
                              <span className="mono">{shortId(outcome.id)}</span>
                            </div>
                          ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ResponsibilityLifecycle({
  responsibility,
}: {
  responsibility: ResponsibilityInspectorModel;
}) {
  const blockers = responsibility.currentResponsibility?.applicableObligationRefs ?? [];
  return (
    <div className="card">
      <h2>Open / Discharged Responsibility</h2>
      {responsibility.responsibilityObligations.length === 0 ? (
        <p className="empty">该 workflow 没有持久化 responsibility obligation。</p>
      ) : (
        <div className="work-items">
          {responsibility.responsibilityObligations.map((obligation) => (
            <div key={obligation.id} className="work-item">
              <div className="work-item-head">
                <strong>{obligation.reason}</strong>
                <span>{obligation.status}</span>
              </div>
              <div className="work-item-meta">
                <span className="detail-label">source</span>
                <span className="mono">
                  {obligation.sourceKind}:{obligation.sourceRef}
                </span>
                <span className="detail-label">subject</span>
                <span className="mono">{obligation.subjectRef}</span>
                <span className="detail-label">opened</span>
                <span>{formatTime(obligation.createdAt)}</span>
                <span className="detail-label">discharged</span>
                <span>{formatTime(obligation.dischargedAt)}</span>
              </div>
              <div>
                <span className="detail-label">blocks current use</span>{" "}
                <strong>{blockers.includes(obligation.id) ? "yes" : "no"}</strong>
              </div>
              <div>
                <span className="detail-label">scope</span>
                <Scope scope={obligation.scope} />
              </div>
              <div>
                <span className="detail-label">projection refs</span>{" "}
                <span className="mono">{obligation.projectionRefs.join(", ") || "—"}</span>
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="muted">
        “blocks current use” 只读取 backend 返回的 applicableObligationRefs，不在浏览器中重新做 scope matching。
      </p>
    </div>
  );
}

export default function ResponsibilityInspector({
  responsibility,
}: {
  responsibility: ResponsibilityInspectorModel;
}) {
  const returnFinancial = responsibility.workflow.workflowType === "return-to-refund";
  const unboundDecisions = responsibility.decisions.filter(
    (decision) =>
      !responsibility.authorizations.some(
        (authorization) => authorization.decisionRef === decision.id,
      ),
  );

  return (
    <>
      <div className="card">
        <h2>Responsibility Summary</h2>
        <div className="detail-header">
          <span className="detail-label">read model</span>
          <span>{responsibility.schema}</span>
          <span className="detail-label">authority bearing</span>
          <State value={responsibility.authorityBearing} />
          <span className="detail-label">open responsibility</span>
          <span>{responsibility.openResponsibility.length}</span>
          <span className="detail-label">authorizations</span>
          <span>{responsibility.authorizations.length}</span>
        </div>
      </div>

      <CurrentVsHistorical responsibility={responsibility} />

      <div className="card">
        <h2>{returnFinancial ? "Financial Responsibilities" : "Responsibility Chain"}</h2>
        <p className="muted">
          Decision → Authorization → Effect → Reality → Outcome；所有连接仅按 backend refs 连接。
        </p>
        {responsibility.authorizations.length === 0 ? (
          <p className="empty">暂无 ExecutionAuthorization。</p>
        ) : (
          <div className="work-items">
            {responsibility.authorizations.map((authorization) => (
              <AuthorizationChain
                key={authorization.id}
                authorization={authorization}
                responsibility={responsibility}
              />
            ))}
          </div>
        )}
        {unboundDecisions.length > 0 ? (
          <details>
            <summary>Decisions without ExecutionAuthorization ({unboundDecisions.length})</summary>
            <ul>
              {unboundDecisions.map((decision) => (
                <li key={decision.id}>
                  <span className="mono">{shortId(decision.id)}</span> ·{" "}
                  {decision.requiredRoles.join(", ") || "—"} / {decision.nextStep ?? "—"}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>

      <ExecutionRealityOutcome responsibility={responsibility} />
      <ResponsibilityLifecycle responsibility={responsibility} />

      <div className="card">
        <h2>Semantic Separations</h2>
        <ul>
          {responsibility.shortcuts.map((shortcut) => (
            <li key={shortcut} className="mono">
              {shortcut}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
