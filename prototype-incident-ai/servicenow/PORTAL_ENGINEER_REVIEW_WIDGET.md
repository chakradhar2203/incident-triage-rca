# ServiceNow Service Portal Widget: AI RCA Engineer Review

Use this Service Portal widget to let engineers review, accept, or reject AI-generated Root Cause Analysis directly from ServiceNow Service Portal (`/sp`).

---

## 1. Widget Metadata

- **Name:** `AI RCA Engineer Review`
- **ID:** `ai-rca-engineer-review`
- **Description:** `Engineer review buttons (Accept, Reject, Request Evidence) for AI-generated RCA.`

---

## 2. Body HTML Template

```html
<div class="panel panel-default ai-review-card" ng-if="c.data.incident">
  <div class="panel-heading">
    <div class="header-content">
      <h3 class="panel-title">
        <i class="fa fa-magic"></i> AI RCA Review &mdash; {{c.data.incident.number}}
      </h3>
      <span class="badge" ng-class="{
        'badge-warning': c.data.incident.status == 'pending' || c.data.incident.status == 'needs_evidence',
        'badge-success': c.data.incident.status == 'approved',
        'badge-danger': c.data.incident.status == 'rejected'
      }">
        Status: {{c.data.incident.status | uppercase}}
      </span>
    </div>
  </div>

  <div class="panel-body">
    <!-- Short Description -->
    <div class="row m-b-sm">
      <div class="col-xs-12">
        <strong>Issue:</strong> {{c.data.incident.short_description}}
      </div>
    </div>

    <!-- AI Diagnostic Summary -->
    <div class="alert alert-info">
      <div class="row">
        <div class="col-sm-9">
          <strong><i class="fa fa-stethoscope"></i> AI Probable Root Cause:</strong>
          <p class="m-t-xs">{{c.data.incident.probable_root_cause || 'Analysis underway or unverified hypothesis.'}}</p>
        </div>
        <div class="col-sm-3 text-right">
          <strong>Confidence:</strong>
          <div class="confidence-val">{{c.data.incident.confidence ? (c.data.incident.confidence * 100).toFixed(0) + '%' : 'N/A'}}</div>
        </div>
      </div>
    </div>

    <!-- Review Form Section -->
    <div class="review-inputs m-t-md" ng-if="c.data.incident.status == 'pending' || c.data.incident.status == 'needs_evidence'">
      <div class="form-group">
        <label for="validatedCause"><strong>Engineer Validated Root Cause (Optional / Edits):</strong></label>
        <textarea id="validatedCause" class="form-control" rows="2" 
                  ng-model="c.form.validated_root_cause" 
                  placeholder="Confirm or refine the root cause for knowledge reuse..."></textarea>
      </div>

      <div class="form-group">
        <label for="engineerNotes"><strong>Engineer Review Notes (Required for Reject/Evidence):</strong> <span class="text-danger">*</span></label>
        <textarea id="engineerNotes" class="form-control" rows="3" 
                  ng-model="c.form.engineer_notes" 
                  placeholder="Explain why you are accepting, rejecting, or requesting more evidence..."></textarea>
      </div>

      <!-- Action Buttons -->
      <div class="action-buttons m-t-md text-right">
        <button type="button" class="btn btn-danger m-r-sm" ng-click="c.submitReview('rejected')" ng-disabled="c.submitting">
          <i class="fa fa-times"></i> Reject RCA
        </button>
        <button type="button" class="btn btn-warning m-r-sm" ng-click="c.submitReview('needs_evidence')" ng-disabled="c.submitting">
          <i class="fa fa-question-circle"></i> Request More Evidence
        </button>
        <button type="button" class="btn btn-success" ng-click="c.submitReview('approved')" ng-disabled="c.submitting">
          <i class="fa fa-check"></i> Accept / Approve RCA
        </button>
      </div>
    </div>

    <!-- Already Reviewed Message -->
    <div class="alert alert-success m-t-md" ng-if="c.data.incident.status == 'approved'">
      <i class="fa fa-check-circle"></i> AI RCA was approved by <strong>{{c.data.incident.reviewed_by}}</strong> at {{c.data.incident.reviewed_at}}.
    </div>

    <div class="alert alert-danger m-t-md" ng-if="c.data.incident.status == 'rejected'">
      <i class="fa fa-times-circle"></i> AI RCA was rejected by <strong>{{c.data.incident.reviewed_by}}</strong> at {{c.data.incident.reviewed_at}}.
    </div>
  </div>
</div>
```

---

## 3. Client Controller

```javascript
api.controller = function($scope, spUtil) {
  var c = this;
  c.submitting = false;
  c.form = {
    engineer_notes: '',
    validated_root_cause: ''
  };

  // Prefill validated root cause from AI suggestion
  if (c.data.incident) {
    c.form.validated_root_cause = c.data.incident.validated_root_cause || c.data.incident.probable_root_cause || '';
    c.form.engineer_notes = c.data.incident.engineer_notes || '';
  }

  c.submitReview = function(decision) {
    if ((decision === 'rejected' || decision === 'needs_evidence') && !c.form.engineer_notes.trim()) {
      spUtil.addErrorMessage('Engineer notes are required when rejecting or requesting more evidence.');
      return;
    }

    if (decision === 'approved' && !c.form.engineer_notes.trim() && !c.form.validated_root_cause.trim()) {
      spUtil.addErrorMessage('Provide either Engineer Notes or a Validated Root Cause to approve.');
      return;
    }

    c.submitting = true;
    c.data.action = 'submit_review';
    c.data.decision = decision;
    c.data.engineer_notes = c.form.engineer_notes;
    c.data.validated_root_cause = c.form.validated_root_cause;

    c.server.update().then(function(response) {
      c.submitting = false;
      if (response && response.success) {
        spUtil.addInfoMessage(response.message || 'Review submitted successfully!');
      } else {
        spUtil.addErrorMessage((response && response.error) || 'Failed to submit review.');
      }
    });
  };
};
```

---

## 4. Server Script

```javascript
(function() {
  var sysId = $sp.getParameter('sys_id');
  if (!sysId) {
    data.incident = null;
    return;
  }

  var gr = new GlideRecord('incident');
  if (!gr.get(sysId)) {
    data.incident = null;
    return;
  }

  // Handle Review Submission
  if (input && input.action == 'submit_review') {
    gr.u_ai_review_status = input.decision;
    gr.u_ai_engineer_notes = input.engineer_notes;
    gr.u_ai_validated_root_cause = input.validated_root_cause;
    gr.u_ai_reviewed_by = gs.getUserID();
    gr.u_ai_reviewed_at = new GlideDateTime();
    gr.update();

    data.success = true;
    data.message = 'AI RCA marked as ' + input.decision.toUpperCase() + ' and synced.';
  }

  // Load Incident & AI Data
  data.incident = {
    sys_id: gr.getUniqueValue(),
    number: gr.getValue('number'),
    short_description: gr.getValue('short_description'),
    status: gr.getValue('u_ai_review_status') || 'pending',
    confidence: parseFloat(gr.getValue('u_ai_confidence') || '0'),
    probable_root_cause: gr.getValue('u_ai_validated_root_cause') || gr.getValue('short_description'),
    engineer_notes: gr.getValue('u_ai_engineer_notes') || '',
    validated_root_cause: gr.getValue('u_ai_validated_root_cause') || '',
    reviewed_by: gr.u_ai_reviewed_by.getDisplayValue(),
    reviewed_at: gr.u_ai_reviewed_at.getDisplayValue()
  };
})();
```

---

## 5. CSS / SCSS

```css
.ai-review-card {
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

.ai-review-card .header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.confidence-val {
  font-size: 20px;
  font-weight: 700;
  color: #1a73e8;
}

.action-buttons button {
  min-width: 140px;
}
```
