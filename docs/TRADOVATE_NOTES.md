# Tradovate integration notes

Distilled from the official docs (api.tradovate.com, partner.tradovate.com,
example-api-js, example-api-faq) and community forum threads. Where
something disagreed with `docs/HANDOFF_TO_COWORK.md`, **HANDOFF wins** — it
is more conservative and encodes lessons learned.

Status: written from docs, not yet empirically verified against paper. All
"verify" items become level-5 integration assertions once creds exist.

---

## 1. Environments & base URLs

| Purpose                | Demo (paper)                                     | Live (funded)                                    |
| ---------------------- | ------------------------------------------------ | ------------------------------------------------ |
| REST                   | `https://demo.tradovateapi.com/v1`               | `https://live.tradovateapi.com/v1`               |
| Trading/account WS     | `wss://demo.tradovateapi.com/v1/websocket`       | `wss://live.tradovateapi.com/v1/websocket`       |
| Market data WS         | `wss://md-demo.tradovateapi.com/v1/websocket` †  | `wss://md.tradovateapi.com/v1/websocket` †       |

† The market-data host split (`md-demo` vs `md`) follows the JS SDK
convention; verify at first successful connect and correct here if wrong.
Flagged in `docs/IMPLEMENTATION_NOTES.md`.

Env select via `.env: TV_ENV=demo|live`. Code path for `live` never runs
from automated flows — only via explicit human CLI invocation (Hard Rule 3).

---

## 2. Authentication

### Initial login — `POST /auth/accesstokenrequest`

Request body (JSON):

```json
{
  "name":        "<username>",
  "password":    "<password>",
  "appId":       "<app name>",
  "appVersion":  "<semver>",
  "deviceId":    "<uuid4, stable per install>",
  "cid":         "<client id from Tradovate>",
  "sec":         "<client secret from Tradovate>"
}
```

Response (`AccessTokenResponse`):

```json
{
  "accessToken":            "<jwt>",
  "expirationTime":         "2026-04-14T20:30:00.000Z",
  "passwordExpirationTime": "...",
  "userStatus":             "Active",
  "userId":                 1234567,
  "name":                   "<username>",
  "hasLive":                false,
  "errorText":              null
}
```

Bearer the returned token in all subsequent REST: `Authorization: Bearer <token>`.

### Lifetime & renewal — **critical**

- Tokens last **90 minutes** from `/auth/accesstokenrequest`.
- Renew via `GET /auth/renewAccessToken` with the current token in `Authorization`.
  Returns a fresh `AccessTokenResponse` (same session, new expiry).
- **Renew at ~75 minutes elapsed** (handoff spec; partner docs say 85 min /
  15 min before expiry). Target 75 for safety margin.
- **Never** re-call `/auth/accesstokenrequest` to refresh. It starts a
  *new* session. Users are limited to 2 concurrent sessions; the third
  silently boots the oldest. A botched refresh path will periodically eject
  itself or the user's UI session.

### Session limit awareness

Calls that would implicitly create a second session (e.g. the executor on
one host while the shadow is on another) should share a single auth via
the state store rather than log in twice. Document this at the auth module
level.

---

## 3. WebSocket frame protocol

Both the trading socket and market-data socket speak the same framing.
**Text frames only.** Each incoming WS text frame begins with a single
ASCII character ("frame prefix") that classifies the payload:

| Prefix | Meaning   | Payload                                             |
| ------ | --------- | --------------------------------------------------- |
| `o`    | open      | none — sent once by server at connect               |
| `h`    | heartbeat | none — server keep-alive (also client must send)    |
| `a`    | array     | JSON array of server messages                       |
| `c`    | close     | JSON `[code, reason]` — server-initiated close       |

A receive loop dispatches on `frame[0]` and parses `frame[1:]` as JSON for
`a` and `c`. `o` and `h` carry no payload.

### Client heartbeat

The client **must** transmit `[]` (literal two-byte text `[]`) every
**~2.5 seconds**. If the client goes silent too long the server closes
the socket. If the server goes silent — no `h`, `a`, or anything else —
for **>7 seconds**, treat the socket as dead regardless of what the OS
thinks. Force-close and reconnect. (Handoff rule, matches forum guidance
of "2–2.5s heartbeat" plus a 3× safety factor.)

Note: while a market-data subscription is actively streaming quotes, the
server may suppress `h` frames because every `a` frame is itself liveness
evidence. The 7s stale check must therefore reset on ANY frame, not just `h`.

### Request framing — plain text, NOT JSON

Outbound requests on the trading WS are plain-text newline-delimited:

```
operation
<id>
<query-string>
<empty line>
<body-json-or-empty>
```

Four segments separated by `\n`. The empty query line is required even
when empty. Example authorize request (sent immediately after receiving `o`):

```
authorize
1

<accessToken>
```

Success reply comes in the next `a` frame:

```
a[{"s":200,"i":1}]
```

- `s` is an HTTP-style status (200/400/401/etc.).
- `i` is the echo of the outbound request id, for correlation.

The server may also push unsolicited events (subscribed quotes, order
updates, etc.) in `a` frames with no `i` field — those are events, not
replies.

### Market-data WS

Same framing. Subscription is done via requests like
`md/subscribeQuote\n2\n\n{"symbol":"MNQM6"}`. Beyond frame handling the
market-data socket is out of Step 1 scope; we only need to *prove* we can
open it and authenticate during the smoke test.

### Reconnect policy

- Exponential backoff, capped at ~60s.
- On every reconnect, re-authorize and re-subscribe before resuming.
- After 3 consecutive failed reconnects, surface to the executor as a
  **hard disconnect**; executor policy is to flatten existing positions
  via REST (which still works on the sibling token) and stop new entries.

---

## 4. Order placement

### Single order — `POST /order/placeorder`

Body (JSON):

```json
{
  "accountId":    123456,
  "accountSpec":  "DEMO123456",
  "action":       "Buy" | "Sell",
  "symbol":       "MNQM6",
  "orderQty":     1,
  "orderType":    "Market" | "Limit" | "Stop" | "StopLimit",
  "price":        18234.25,           // for Limit / StopLimit
  "stopPrice":    18220.00,           // for Stop / StopLimit
  "timeInForce":  "Day" | "GTC" | "IOC" | "FOK",
  "isAutomated":  false
}
```

Response: `{"orderId": <int>}` on success. Rejects come back with
`errorText` populated.

### OCO bracket — `POST /orderStrategy/startorderstrategy`

This is **the** way we enter positions. Atomic: the server creates entry
+ protective OCO in one transaction. If anything after this races, the
executor must immediately market-close (Hard Rule: "no unprotected
position, ever").

Body (JSON):

```json
{
  "accountId":           123456,
  "accountSpec":         "DEMO123456",
  "symbol":              "MNQM6",
  "action":              "Buy",           // entry direction
  "orderStrategyTypeId": 2,                // 2 = built-in Brackets (only type)
  "params":              "<stringified JSON>"
}
```

`params` is **a JSON-encoded string**, not a nested JSON object. Inside:

```json
{
  "entryVersion": {
    "orderType": "Market",
    "orderQty":  1,
    "price":     18234.25               // only for Limit entries
  },
  "brackets": [
    {
      "qty":           1,
      "profitTarget":  8,                 // ticks from entry (+ = favorable)
      "stopLoss":     -12,                // ticks from entry (- = against)
      "trailingStop":  false
    }
  ]
}
```

Units: `profitTarget` and `stopLoss` are **in ticks from entry**, signed
by direction-of-favor (profit positive, stop negative). The handoff spec
demands tick-aligned stops, so these are always integers on our side.

For a Sell entry, the same signs apply — profit positive means "better
than entry" which for a short means lower price.

Response: `{"orderStrategy": {"id": <int>, ...}}`. Keep the id for
lifecycle tracking (modify/cancel via `/orderStrategy/modify` and
`/orderStrategy/cancel`).

### Cancel — `POST /order/cancelorder`

Body: `{"orderId": <int>}`.

### `accountSpec` lookup

One-time per user/session: `GET /account/list`, pick the account with the
matching `name`/`id` the user configured in `.env:TV_ACCOUNT_ID`, keep
`id` and `name` for subsequent order payloads. Cache in memory for the
session.

---

## 5. Symbol conventions

MNQ futures symbols follow the CME month codes:
`MNQ{MonthCode}{YearLastDigit}` — e.g. `MNQM6` = June 2026.

Month codes: H=Mar, M=Jun, U=Sep, Z=Dec (the front-month quarterly cycle).
Tradovate expects the exact exchange symbol, not the root. The currently
active contract must be resolved at session start via `GET /contract/find?name=MNQ`
which returns the list; we pick the earliest `expirationDate` > today.

---

## 6. Open questions / verify against paper

1. Market-data WS host split: `md-demo.tradovateapi.com` for paper vs
   `md.tradovateapi.com` for live. Assumed from JS SDK pattern; verify
   at first successful connect.
2. Heartbeat tolerance: confirmed ">7s stale = dead" against real traffic.
   Docs just say "2–2.5s send". We use 7s cutoff per handoff, will measure
   actual inter-frame gap during smoke test.
3. `orderStrategyTypeId` numeric values: the forum says `2` for Brackets
   and "it's the only currently supported type". Verify via
   `GET /orderStrategyType/list` at session start and log a warning if the
   numeric id isn't 2 so we aren't silently wrong after a server change.
4. Signed tick semantics on `stopLoss`/`profitTarget` for Sell entries:
   the example above assumes both sides use "+ = favorable, − = against".
   Verify with a 1-contract Sell during smoke test.

---

## Sources

- [Access Token Request — Tradovate Partner API](https://partner.tradovate.com/api/rest-api-endpoints/authentication/access-token-request)
- [Auth Overview — Tradovate Partner API](https://partner.tradovate.com/overview/quick-setup/auth-overview)
- [Stage 1 Authentication — Tradovate Partner API](https://partner.tradovate.com/overview/conformance-testing/stage-1-authentication)
- [example-api-js WebSockets tutorial (EX-05)](https://github.com/tradovate/example-api-js/tree/main/tutorial/WebSockets/EX-05-WebSockets-Start)
- [Long-lived WebSocket Connections — forum](https://community.tradovate.com/t/long-lived-websocket-connections/3064)
- [Starting strategies through API — forum (bracket JSON)](https://community.tradovate.com/t/starting-strategies-through-api/2625)
- [OSO/OCO/BRACKET Orders — forum](https://community.tradovate.com/t/oso-oco-bracket-orders/10272)
