export interface ResponsibilityHistoricalUse {
  subjectType: string;
  subjectRef: string;
  subjectVersion: string;
  judgmentRef: string;
  historicalUseRef: string;
  requirementDigest: string;
  snapshotDigest: string;
  createdAt: string;
}

export interface ResponsibilityCurrent {
  kind: "listing-publication";
  eligible: boolean;
  status: string;
  authorizationCurrent: boolean;
  publicationQualificationCurrent: boolean | null;
  experienceRequired: boolean | null;
  experienceStatus: string;
  portableStatus: string;
  historicalUseRef: string | null;
  requirementDigest: string | null;
  applicableObligationRefs: string[];
  reasons: string[];
  authorityBearing: false;
}

export interface ResponsibilityDecision {
  id: string;
  workItemId: string;
  decision: string;
  userId: string;
  createdAt: string;
  nextStep?: string | null;
  requiredRoles: string[];
  authorizationProfile?: string | null;
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
  scope: Record<string, unknown>;
  policyVersion: string;
  environmentRef: string;
  issuedAt: string;
  issuedByUserId: string;
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
  realizationAssessmentRef: string;
  verificationRefs: string[];
  confirmedAt: string;
  confirmedByUserId: string;
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

export interface ResponsibilityObligation extends OpenResponsibility {
  status: string;
  createdAt: string;
  dischargedAt?: string | null;
  recordedByUserId: string;
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
  currentResponsibility: ResponsibilityCurrent | null;
  historical: ResponsibilityHistoricalUse[];
  decisions: ResponsibilityDecision[];
  authorizations: ResponsibilityAuthorization[];
  execution: ResponsibilityExecution[];
  reality: ResponsibilityReality[];
  confirmedOutcomes: ResponsibilityConfirmedOutcome[];
  responsibilityObligations: ResponsibilityObligation[];
  openResponsibility: OpenResponsibility[];
  shortcuts: string[];
}
