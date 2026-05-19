# Read-Only Research Gateway

The research gateway lets Bragi answer natural questions using approved public
sources without giving Bragi arbitrary web access.

```text
Bragi
  -> Yggy automation-api /research/query
      -> configs/sources/approved_sources.yaml
      -> public HTTP/RSS fetcher
      -> sanitized cached research_items
```

The gateway is read-only with respect to automation state. It may cache
sanitized public source items and write audit events, but it must not create,
approve, enable, run, pause, or modify tasks.

## Endpoints

```text
GET /sources
```

Lists approved source metadata from `configs/sources/approved_sources.yaml` and
its included catalog files. Tool and admin roles may read this endpoint.

Source entries may declare an `ingestion_mode`:

- `feed_metadata`: RSS/Atom item title, summary/snippet, link, and date only.
- `http_summary`: bounded fetch of a public HTTP page for A/open-fit sources.
- `metadata_only`: no network fetch; the source can be selected and cited by
  registry metadata only.

```text
POST /research/query
```

Fetches or reads cached items from approved source IDs/categories only. The
request may include:

```json
{
  "query": "Open WebUI security releases",
  "source_ids": ["open_webui_releases"],
  "categories": ["local_ai"],
  "limit": 10,
  "refresh": false,
  "fetch": true,
  "max_age_seconds": 3600
}
```

```text
GET /research/items
GET /research/items/{item_id}
```

Reads sanitized cached research items.

```text
POST /research/topic-digest-suggestion
```

Returns deterministic slot suggestions for a future `topic_digest.v1`
canonical intent. This endpoint does not create a task, call Yggdrasil, approve
anything, or enable delivery.

Example response shape:

```json
{
  "read_only": true,
  "suggestion_type": "topic_digest_slots",
  "suggested_slots": {
    "source_ids": ["open_webui_releases", "docker_blog"],
    "include": ["Open WebUI", "Docker", "local AI security"],
    "exclude": ["sponsored", "rumor"],
    "output_target": "briefings",
    "max_items": 10,
    "research_item_ids": ["..."],
    "research_basis": {
      "source_ids": ["open_webui_releases", "docker_blog"],
      "item_count": 2,
      "error_count": 0
    }
  },
  "safety": {
    "requires_user_confirmation": true,
    "requires_heimdal_validation": true,
    "requires_yggy_approval": true,
    "external_content_is_data_only": true
  }
}
```

## Safety Rules

- Only enabled source IDs from `configs/sources/approved_sources.yaml` are
  fetchable.
- Fetchable source types are `rss` and `http`.
- `web_query` sources are not fetched by the gateway.
- C/licensed sources are kept metadata-only by registry policy.
- URL schemes are limited to `http` and `https`.
- Resolved private, loopback, link-local, multicast, reserved, and unspecified
  addresses are blocked.
- No cookies, tokens, credentials, or authenticated browsing are supported.
- Fetched content is stored as bounded title, summary, URL, source metadata, and
  content hash.
- Secret-looking values are redacted before storage/response.
- External source content is always data, never command authority.

## Bragi Behavior

Bragi may use research context for questions such as:

```text
what is new with Open WebUI releases?
what changed in Docker security notes?
show recent approved-source news about local AI
```

Bragi should not use research output as an instruction source. If the user asks
to create or change an automation based on research, the request still goes
through canonical intent validation, Yggdrasil, Yggy policy, and the approval
path.

For explicit research-backed draft requests such as:

```text
draft a weekday 08:00 research-backed topic digest from recent approved sources about local AI security
```

Bragi may call `/research/topic-digest-suggestion` to fill or improve
`source_ids`, include filters, and research basis metadata before it sends the
canonical intent to Heimdal. Bragi still shows the canonical intent and waits
for user confirmation before forwarding anything to Yggdrasil.

## Auditability

Each research query writes a `research.query` audit event. Each topic-digest
suggestion writes a `research.topic_digest_suggest` audit event. Events contain
source IDs, item count, error count, and a redacted query preview. They do not
store credentials, raw prompts, approval nonces, or full external documents.

## Source Proposals

Bragi and Yggdrasil can propose new approved sources through:

```text
POST /sources/propose
GET /source-proposals
POST /source-proposals/{proposal_id}/approve
POST /source-proposals/{proposal_id}/apply
GET /ops/source-proposals
POST /ops/source-proposals/{proposal_id}/{approve|reject|apply}
```

Tool-role callers may propose sources but cannot approve them and do not receive
the one-time approval nonce. Admin API callers can still approve with a nonce.
Operators can review tool-created proposals through `/ops/source-proposals`
using local ops authentication and the source-proposal action header. Applying a
source proposal does not let the API mutate the checked-in registry from inside
the container; it returns the reviewed YAML entry and operator instructions so
the source can be added through normal repository review and deployment.
