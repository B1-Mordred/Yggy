# Approval Model

## Levels

- `L0_READ_ONLY`: public fetches, summarization, safe status endpoints.
- `L1_NOTIFY_ONLY`: Discord digest or alert to a whitelisted target.
- `L2_LOCAL_WRITE`: local files, internal notes, task config writes.
- `L3_EXTERNAL_SIDE_EFFECT`: email, ticket creation, SaaS state, external posts.
- `L4_DESTRUCTIVE_OR_SECURITY_SENSITIVE`: deletion, Docker changes, firewall changes, credential rotation, purchases.

## Role Rules

- Tool key can draft, list, request approval, and run approved L0/L1 or dry-run tasks.
- Worker key can perform internal execution reporting and notification calls.
- Admin key can approve and reject. It must never be exposed to the model.
- L4 is manual only. The system may generate instructions, but it must not execute autonomously.

## Nonce

Approval requests include a nonce. The API stores only a hash of that nonce. The local admin CLI submits the nonce with the admin key.
