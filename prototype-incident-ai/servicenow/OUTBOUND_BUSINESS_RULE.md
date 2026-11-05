# ServiceNow Business Rule: Sync Engineer Review to AI Prototype

This Business Rule listens for engineer review status changes on `incident` and sends an outbound REST request to the AI Prototype app (`/api/v1/prototype/reviews/{incident_id}`).

---

## 1. Business Rule Configuration

- **Name:** `Sync AI RCA Review to Prototype`
- **Table:** `Incident [incident]`
- **Active:** `true`
- **Advanced:** `true`
- **When:** `after`
- **Insert:** `false`
- **Update:** `true`
- **Filter Conditions:**
  - `AI Review Status [u_ai_review_status]` **changes** AND
  - `AI Review Status [u_ai_review_status]` **is one of** `approved, needs_evidence, rejected`

---

## 2. Advanced Script

> **Note on App URL**: Replace `http://YOUR_APP_HOST:8000` with your machine's reachable IP/domain or ngrok tunnel URL (e.g., `https://xxxx.ngrok-free.app` if running locally and communicating with a cloud PDI).

```javascript
(function executeRule(current, previous /*null when async*/) {

    // 1. Identify Target Incident ID (number or sys_id)
    var incidentId = current.number.toString();
    if (!incidentId) {
        incidentId = current.sys_id.toString();
    }

    // 2. Prepare Review Payload for FastAPI backend
    var decision = current.u_ai_review_status.toString(); // 'approved', 'rejected', or 'needs_evidence'
    var reviewerName = current.u_ai_reviewed_by.getDisplayValue();
    if (!reviewerName) {
        reviewerName = gs.getUserDisplayName() || gs.getUserName();
    }

    var notes = current.u_ai_engineer_notes.toString() || 'Review submitted in ServiceNow';
    var validatedRootCause = current.u_ai_validated_root_cause.toString() || '';

    var payload = {
        "decision": decision,
        "reviewer": reviewerName,
        "notes": notes,
        "validated_root_cause": validatedRootCause
    };

    // 3. Configure Outbound REST Request
    // Configure your local machine / tunnel URL here:
    var appBaseUrl = gs.getProperty('ai.prototype.base_url', 'http://127.0.0.1:8000');
    var endpoint = appBaseUrl.replace(/\/+$/, '') + '/api/v1/prototype/reviews/' + encodeURIComponent(incidentId);

    try {
        var request = new sn_ws.RESTMessageV2();
        request.setEndpoint(endpoint);
        request.setHttpMethod('POST');
        request.setRequestHeader('Accept', 'application/json');
        request.setRequestHeader('Content-Type', 'application/json');
        request.setRequestBody(JSON.stringify(payload));
        request.setHttpTimeout(10000); // 10 seconds timeout

        var response = request.execute();
        var statusCode = response.getStatusCode();
        var responseBody = response.getBody();

        if (statusCode >= 200 && statusCode < 300) {
            gs.info('AI RCA Review sync successful for ' + incidentId + ': ' + responseBody);
        } else {
            gs.warn('AI RCA Review sync failed for ' + incidentId + ' (HTTP ' + statusCode + '): ' + responseBody);
        }
    } catch (ex) {
        gs.error('Exception during AI RCA Review sync for ' + incidentId + ': ' + ex.getMessage());
    }

})(current, previous);
```

---

## 3. System Property (Optional but Recommended)

Navigate to **System Properties > Create New**:
- **Suffix / Name**: `ai.prototype.base_url`
- **Type**: `string`
- **Value**: Your application's public URL (e.g. `https://your-tunnel.ngrok-free.app` or server address).
