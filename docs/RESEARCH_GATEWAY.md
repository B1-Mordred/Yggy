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

Lists approved source metadata from `configs/sources/approved_sources.yaml`.
Tool and admin roles may read this endpoint.

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

## Safety Rules

- Only enabled source IDs from `configs/sources/approved_sources.yaml` are
  fetchable.
- Fetchable source types are `rss` and `http`.
- `web_query` sources are not fetched by the gateway.
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

## Auditability

Each research query writes a `research.query` audit event containing source IDs,
item count, error count, and a redacted query preview. It does not store
credentials, raw prompts, approval nonces, or full external documents.
