"""Idempotent ServiceNow PDI installer for incident engineer-review controls."""

from __future__ import annotations

from typing import Any

from integrations import ServiceNowClient


FIELDS: tuple[dict[str, Any], ...] = (
    {"element": "u_ai_review_status", "column_label": "AI Review Status", "internal_type": "string", "max_length": "40", "choice": "1", "default_value": "pending"},
    {"element": "u_ai_review_priority", "column_label": "AI Review Priority", "internal_type": "string", "max_length": "40", "choice": "1", "default_value": "standard"},
    {"element": "u_ai_confidence", "column_label": "AI Confidence", "internal_type": "decimal", "read_only": "true"},
    {"element": "u_ai_knowledge_case", "column_label": "AI Knowledge Case", "internal_type": "string", "max_length": "100", "read_only": "true"},
    {"element": "u_ai_review_reason", "column_label": "AI Review Reason", "internal_type": "string", "max_length": "2000", "read_only": "true"},
    {"element": "u_ai_validated_root_cause", "column_label": "AI Validated Root Cause", "internal_type": "html"},
    {"element": "u_ai_engineer_notes", "column_label": "AI Engineer Notes", "internal_type": "html"},
    {"element": "u_ai_reviewed_by", "column_label": "AI Reviewed By", "internal_type": "reference", "reference": "sys_user", "read_only": "true"},
    {"element": "u_ai_reviewed_at", "column_label": "AI Reviewed At", "internal_type": "glide_date_time", "read_only": "true"},
    {"element": "u_ai_synthetic_test", "column_label": "AI Synthetic Test", "internal_type": "boolean", "default_value": "false", "read_only": "true"},
)

CHOICES = {
    "u_ai_review_status": (("pending", "Pending"), ("approved", "Approved"), ("needs_evidence", "Needs Evidence"), ("rejected", "Rejected")),
    "u_ai_review_priority": (("standard", "Standard"), ("required", "Required")),
}

_APPROVE = """(function executeAction(current, action) {
 if (gs.nil(current.u_ai_engineer_notes) && gs.nil(current.u_ai_validated_root_cause)) {
  gs.addErrorMessage('Enter Engineer Notes or a Validated Root Cause before approving the AI RCA.'); action.setRedirectURL(current); return;
 }
 current.u_ai_review_status='approved'; current.u_ai_reviewed_by=gs.getUserID(); current.u_ai_reviewed_at=new GlideDateTime(); current.update();
 gs.addInfoMessage('AI RCA approved. The incident remains active and no knowledge article was published.'); action.setRedirectURL(current);
})(current, action);"""

_NEEDS_EVIDENCE = """(function executeAction(current, action) {
 if (gs.nil(current.u_ai_engineer_notes)) { gs.addErrorMessage('Enter Engineer Notes describing the missing evidence.'); action.setRedirectURL(current); return; }
 current.u_ai_review_status='needs_evidence'; current.u_ai_reviewed_by=gs.getUserID(); current.u_ai_reviewed_at=new GlideDateTime(); current.update();
 gs.addInfoMessage('More evidence requested. The incident remains active.'); action.setRedirectURL(current);
})(current, action);"""

_REJECT = """(function executeAction(current, action) {
 if (gs.nil(current.u_ai_engineer_notes)) { gs.addErrorMessage('Enter Engineer Notes explaining why the AI RCA is rejected.'); action.setRedirectURL(current); return; }
 current.u_ai_review_status='rejected'; current.u_ai_reviewed_by=gs.getUserID(); current.u_ai_reviewed_at=new GlideDateTime(); current.update();
 gs.addInfoMessage('AI RCA rejected. The original AI work notes remain available for audit.'); action.setRedirectURL(current);
})(current, action);"""

ACTIONS: tuple[dict[str, str], ...] = (
    {"name": "Approve AI RCA", "action_name": "approve_ai_rca", "order": "100", "condition": "current.u_ai_review_status == 'pending' || current.u_ai_review_status == 'needs_evidence'", "script": _APPROVE},
    {"name": "Request More Evidence", "action_name": "request_more_ai_evidence", "order": "110", "condition": "current.u_ai_review_status == 'pending'", "script": _NEEDS_EVIDENCE},
    {"name": "Reject AI RCA", "action_name": "reject_ai_rca", "order": "120", "condition": "current.u_ai_review_status == 'pending' || current.u_ai_review_status == 'needs_evidence'", "script": _REJECT},
)


def install_review_controls(client: ServiceNowClient) -> dict[str, object]:
    """Create missing fields, choices, and UI Actions; never overwrite existing records."""

    result: dict[str, list[str]] = {"created": [], "existing": []}
    for field in FIELDS:
        element = field["element"]
        found = client.list_records("sys_dictionary", f"name=incident^element={element}", "sys_id,element", 1)
        if found:
            result["existing"].append(f"field:{element}")
        else:
            client.create_record("sys_dictionary", {"name": "incident", "active": "true", **field})
            result["created"].append(f"field:{element}")

    for element, choices in CHOICES.items():
        for sequence, (value, label) in enumerate(choices, 100):
            found = client.list_records(
                "sys_choice", f"name=incident^element={element}^value={value}", "sys_id,value", 1
            )
            key = f"choice:{element}:{value}"
            if found:
                result["existing"].append(key)
            else:
                client.create_record(
                    "sys_choice",
                    {"name": "incident", "element": element, "value": value, "label": label, "sequence": str(sequence), "inactive": "false"},
                )
                result["created"].append(key)

    for definition in ACTIONS:
        found = client.list_records(
            "sys_ui_action", f"table=incident^action_name={definition['action_name']}", "sys_id,action_name", 1
        )
        key = f"action:{definition['action_name']}"
        if found:
            result["existing"].append(key)
        else:
            client.create_record(
                "sys_ui_action",
                {"table": "incident", "active": "true", "form_button": "true", "client": "false", **definition},
            )
            result["created"].append(key)

    return {**result, "created_count": len(result["created"]), "existing_count": len(result["existing"])}
