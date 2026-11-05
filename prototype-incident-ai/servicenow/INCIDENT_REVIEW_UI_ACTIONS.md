# Incident engineer-review UI Actions

Target table: `Incident [incident]`

These are server-side form buttons. They update only AI review fields. They do not resolve the incident or publish a knowledge article.

## Approve AI RCA

- Name: `Approve AI RCA`
- Action name: `approve_ai_rca`
- Form button: enabled
- Order: `100`
- Condition:

```javascript
current.u_ai_review_status == 'pending' || current.u_ai_review_status == 'needs_evidence'
```

- Script:

```javascript
(function executeAction(current, action) {
    if (gs.nil(current.u_ai_engineer_notes) && gs.nil(current.u_ai_validated_root_cause)) {
        gs.addErrorMessage('Enter Engineer Notes or a Validated Root Cause before approving the AI RCA.');
        action.setRedirectURL(current);
        return;
    }

    current.u_ai_review_status = 'approved';
    current.u_ai_reviewed_by = gs.getUserID();
    current.u_ai_reviewed_at = new GlideDateTime();
    current.update();

    gs.addInfoMessage('AI RCA approved. The incident remains active and no knowledge article was published.');
    action.setRedirectURL(current);
})(current, action);
```

## Request More Evidence

- Name: `Request More Evidence`
- Action name: `request_more_ai_evidence`
- Form button: enabled
- Order: `110`
- Condition:

```javascript
current.u_ai_review_status == 'pending'
```

- Script:

```javascript
(function executeAction(current, action) {
    if (gs.nil(current.u_ai_engineer_notes)) {
        gs.addErrorMessage('Enter Engineer Notes describing the missing evidence.');
        action.setRedirectURL(current);
        return;
    }

    current.u_ai_review_status = 'needs_evidence';
    current.u_ai_reviewed_by = gs.getUserID();
    current.u_ai_reviewed_at = new GlideDateTime();
    current.update();

    gs.addInfoMessage('More evidence requested. The incident remains active.');
    action.setRedirectURL(current);
})(current, action);
```

## Reject AI RCA

- Name: `Reject AI RCA`
- Action name: `reject_ai_rca`
- Form button: enabled
- Order: `120`
- Condition:

```javascript
current.u_ai_review_status == 'pending' || current.u_ai_review_status == 'needs_evidence'
```

- Script:

```javascript
(function executeAction(current, action) {
    if (gs.nil(current.u_ai_engineer_notes)) {
        gs.addErrorMessage('Enter Engineer Notes explaining why the AI RCA is rejected.');
        action.setRedirectURL(current);
        return;
    }

    current.u_ai_review_status = 'rejected';
    current.u_ai_reviewed_by = gs.getUserID();
    current.u_ai_reviewed_at = new GlideDateTime();
    current.update();

    gs.addInfoMessage('AI RCA rejected. The original AI work notes remain available for audit.');
    action.setRedirectURL(current);
})(current, action);
```

## Create KB Draft

Create this only after the three review buttons have been tested.

- Name: `Create KB Draft`
- Action name: `create_ai_kb_draft`
- Form button: enabled
- Order: `130`
- Condition:

```javascript
current.u_ai_review_status == 'approved' &&
!gs.nil(current.close_notes) &&
!gs.nil(current.u_ai_validated_root_cause) &&
(current.state == 6 || current.state == 7)
```

The implementation must create a `kb_knowledge` record in draft state and copy only the engineer-validated root cause and resolution. It must not call a publish workflow.

## Data Policy

Use a Data Policy on Incident so API updates follow the same rule as the UI:

- When `u_ai_review_status` becomes `approved`, at least one of `u_ai_engineer_notes` or `u_ai_validated_root_cause` must be populated.
- When it becomes `needs_evidence` or `rejected`, `u_ai_engineer_notes` is mandatory.
- Integration accounts must not be allowed to set `u_ai_reviewed_by`, `u_ai_reviewed_at`, incident resolution fields, or knowledge publication state.

