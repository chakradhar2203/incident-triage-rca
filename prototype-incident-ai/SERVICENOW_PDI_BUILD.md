# ServiceNow PDI build specification

This specification is for the developer instance. It deliberately separates AI recommendations, engineer review, incident resolution, and knowledge publication.

## Incident fields

Create these fields on `Incident [incident]` using the instance's application scope and naming conventions:

| Label | Suggested name | Type | Purpose |
|---|---|---|---|
| AI Review Status | `u_ai_review_status` | Choice | `pending`, `approved`, `needs_evidence`, `rejected` |
| AI Review Priority | `u_ai_review_priority` | Choice | `standard`, `required` |
| AI Confidence | `u_ai_confidence` | Decimal | Grounded RCA confidence from 0 through 1 |
| AI Knowledge Case | `u_ai_knowledge_case` | String (100) | Retrieved or seeded case identifier |
| AI Review Reason | `u_ai_review_reason` | String (2000) | Why mandatory review was triggered |
| AI Validated Root Cause | `u_ai_validated_root_cause` | HTML or large string | Engineer-confirmed cause |
| AI Engineer Notes | `u_ai_engineer_notes` | HTML or large string | Evidence checked and corrections |
| AI Reviewed By | `u_ai_reviewed_by` | Reference to User | Reviewing engineer |
| AI Reviewed At | `u_ai_reviewed_at` | Date/Time | Review timestamp |
| AI Synthetic Test | `u_ai_synthetic_test` | True/False | Separates PDI seed incidents from operational records |

Place the fields in a form section named **AI Triage & Engineer Validation**. Make confidence, case ID, priority and reason read-only for normal engineers. Review status, validated root cause and engineer notes are changed through UI Actions.

## UI Actions

Create three form buttons on Incident:

1. **Approve AI RCA**
   - Visible when AI Review Status is `pending` or `needs_evidence`.
   - Requires engineer notes or a validated root cause.
   - Sets status to `approved`, reviewer to the current user, and reviewed time to now.
   - Does not resolve the incident and does not publish knowledge.

2. **Request More Evidence**
   - Visible when status is `pending`.
   - Requires engineer notes describing the missing evidence.
   - Sets status to `needs_evidence`, reviewer and reviewed time.
   - Leaves the incident active.

3. **Reject AI RCA**
   - Visible when status is `pending` or `needs_evidence`.
   - Requires a rejection reason.
   - Sets status to `rejected`, reviewer and reviewed time.
   - Does not delete AI work notes.

Add a fourth action later, **Create KB Draft**, visible only when the incident is resolved and AI Review Status is `approved`. It must create a draft article for knowledge-manager review; it must never publish directly.

## Form controls and policies

- Show a red banner when review priority is `required` and status is not `approved`.
- Prevent the custom Create KB Draft action for `pending`, `needs_evidence`, or `rejected` records.
- Require resolution code, resolution notes and validated root cause before a resolved incident can be promoted.
- Keep synthetic incidents visibly marked and exclude them from operational SLA/reporting dashboards.
- Restrict the integration user to create/update incidents and the listed AI fields. It must not close incidents or publish knowledge.

## Integration sequence

1. Python creates a synthetic PDI incident every two minutes and records its source case ID.
2. The RCA service writes analysis and review disposition to the incident.
3. An engineer uses one of the review buttons.
4. A Business Rule sends the review decision to `POST /api/v1/prototype/reviews/{incident_id}`.
5. After real resolution, a separate Business Rule sends verified resolution feedback.
6. Only verified resolution feedback is promoted into the production Firestore collection.

## PDI build order

1. Create or confirm the integration user and minimum roles.
2. Create the custom fields and form section.
3. Add the three review UI Actions.
4. Add UI Policies and validation rules.
5. Configure inbound/outbound REST and Business Rules.
6. Test with one synthetic incident before starting the two-minute recurrence.
7. Confirm duplicate prevention, retry handling and the engineer-review audit trail.
