# Google Calendar Integration

AJA can read your upcoming Google Calendar events and mirror them into its
bi-temporal knowledge graph so missions can reason about your schedule.

## 1. Google Cloud Console Setup

1. Go to https://console.cloud.google.com/ and create (or pick) a project.
2. Navigate to **APIs & Services > Library**, search for **Google Calendar
   API**, and click **Enable**.
3. Go to **APIs & Services > OAuth consent screen**:
   - User type: **External** (personal Gmail) or Internal (Workspace).
   - Add the scope `https://www.googleapis.com/auth/calendar`.
   - Add your own account as a **Test user** while in testing mode.
4. Go to **APIs & Services > Credentials > Create credentials > OAuth client
   ID**:
   - Application type: **Desktop app**.
   - Download the JSON file (e.g. `client_secret_xxx.json`).

## 2. Configure the Client

Either point AJA at the downloaded file:

```bash
set GOOGLE_CALENDAR_CLIENT_SECRET=C:\path\to\client_secret_xxx.json
```

or provide raw values:

```bash
set GOOGLE_CALENDAR_CLIENT_ID=1234.apps.googleusercontent.com
set GOOGLE_CALENDAR_CLIENT_SECRET=GOCSPX-...
```

## 3. Connect

```bash
aja calendar connect
```

A browser window opens for consent; after approval the refresh token is
stored automatically. Verify with `aja calendar status` (reports connected /
not connected). To revoke, run `aja calendar disconnect` and also remove
AJA under your Google Account **Security > Third-party access**.

Optional config in `aja.json`:

```json
{
  "google_calendar": {
    "enabled": true,
    "calendar_ids": ["primary"],
    "sync_interval_minutes": 60
  }
}
```

`google_calendar` is optional; defaults are disabled / `["primary"]` / 60.

## Security Notes

- The OAuth **refresh token is stored in the OS keyring** (service `AJA`,
  username `gcal`) — never in plaintext configs.
- A fallback copy is written to the gitignored `.env` with OS ACLs
  restricted to your user only (icacls on Windows, chmod 600 on POSIX), so
  headless hosts without a keyring backend still work.
- Access tokens auto-refresh and rotated refresh tokens are re-persisted.
- Revocation: `aja calendar disconnect` deletes both stored copies; you can
  additionally revoke at https://myaccount.google.com/permissions.
- Scope requested is calendar-only; no drive/gmail scopes are requested.

## Programmatic API

```python
from aja.calendar import events, graph_sync

events.list_events("2026-08-23T00:00:00Z", "2026-08-24T00:00:00Z")
events.create_event("Standup", "2026-08-24T09:00:00Z", "2026-08-24T09:15:00Z")
graph_sync.sync_to_graph(days_ahead=7)
graph_sync.events_between("2026-08-23T00:00:00Z", "2026-08-30T00:00:00Z")
```

If `google-api-python-client` / `google-auth-oauthlib` are not installed,
calendar features degrade cleanly (`is_connected()` returns False;
`get_service()` raises ImportError with an actionable message).
