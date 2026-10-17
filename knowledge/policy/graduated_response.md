# Demo Bank graduated scam-response policy (fictional, for this prototype)

## ALLOW
The transfer is sent. No friction. The decision and its scores are still logged to the tamper-evident audit trail.

## NUDGE
Show an in-app warning that names the specific risk signal (for example a first payment to a new payee). The customer may continue after confirming the payee. Budget: at most 1% of legitimate digital transfers may be nudged.

## HOLD
Pause the transfer for up to 24 hours. The fraud team calls the customer on the registered number only, never on a number supplied in the session. If the customer has registered a trusted contact, alert them immediately. The customer can cancel at any time; release requires a verbal check using the scam-awareness script for the detected typology. Budget: at most 0.2% of legitimate digital transfers may be held.

## BLOCK
Stop the transfer and lock outgoing digital transfers for 24 hours (money-lock style). The fraud team calls the customer and the trusted contact. If the customer still insists on paying a likely scammer, escalate to the police anti-scam centre; a restriction order is a last resort. Budget: at most 0.05% of legitimate digital transfers may be blocked.

## Payee (mule) handling
When the payee-risk model flags the receiving account, file a suspicious transaction report and notify the receiving bank through the inter-bank fraud channel so the mule account can be reviewed.

## Evidence and fairness
Every NUDGE, HOLD and BLOCK records its scores, reason codes and model versions in the hash-chained audit log. Protected attributes such as age are never model inputs; friction rates are monitored by customer segment.
