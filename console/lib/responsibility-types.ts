export interface ResponsibilityHistoricalUse {
  subjectType: string;
  subjectRef: string;
  subjectVersion: string;
  judgmentRef: string;
  historicalUseRef: string;
}

export interface ResponsibilityDecision {
  id: string;
  workItemId: string;
  decision: string;
  userId: string;
  createdAt: string;
  authorityBearing: false;
}

export interface ResponsibilityAuthorization {
  id: string;
  decisionRef: string;
  subjectType: string;
  subjectRef: string;
  subjectVersion?: string | null;
  targetSystem: string;
  allowedOperations: string[];
  policyVersion: string;
  environmentRef: string;
  expiresAt?: string | null;
  revokedAt?: string | null;
}

export interface ResponsibilityExecution {
  effectId: string;
  intentId: string;
  operation: string;
  status: string;
  workflowRef?: string | null;
  authorizationRef?: string | null;
  legacyApprovalRef?: string | null;
}

export interface ResponsibilityReality {
  assessmentId: string;
  effectId: string;
  status: string;
  evidenceRefs: string[];
}

export interface ResponsibilityConfirmedOutcome {
  id: string;
  effectId: string;
  outcomeType: string;
  verificationRefs: string[];
  authorityBearing: false;
}

export interface OpenResponsibility {
  id: string;
  subjectRef: string;
  sourceKind: string;
  sourceRef: string;
  reason: string;
  scope: Record<string, unknown>;
  projectionRefs: string[];
}

export interface ResponsibilityInspector {
  schema: "commerce-responsibility-inspector-v1";
  authorityBearing: false;
  workflow: {
    id: string;
    workflowType: string;
    status: string;
    boundedCompletionOnly: true;
  };
  historical: ResponsibilityHistoricalUse[];
  decisions: ResponsibilityDecision[];
  authorizations: ResponsibilityAuthorization[];
  execution: ResponsibilityExecution[];
  reality: ResponsibilityReality[];
  confirmedOutcomes: ResponsibilityConfirmedOutcome[];
  openResponsibility: OpenResponsibility[];
  shortcuts: string[];
}
