# Client Bearer Token Setting — Design

Date: 2026-09-07
Scope: Flutter client only (`client/`). No server changes.

## Problem

`AuthRequiredBanner` tells the user to "set a bearer token in Settings", but the
Flutter client has no such setting: `authTokenProvider`
(`client/lib/data/providers/repository_providers.dart:38`) is declared and read
by `ApiClient` but never written anywhere in `client/lib`. So a backend run with
`OMNISCRIBE_AUTH_TOKEN` armed (`src/omniscribe/middleware/auth.py`) rejects every
non-exempt route with 401 and the client has no way to satisfy it.

A second, latent defect blocks the same path: `ApiClient`'s request interceptor
assigns `options.headers['Authorization'] = 'Bearer $token'` unconditionally
(`client/lib/core/network/api_client.dart:111-117`), so once a global token
exists it overwrites the per-request bearer token that artifact downloads supply
themselves (`ocr_repository.dart` `getTextArtifact`, `getOcrResultBytes`). Those
calls would start failing 403. This is dormant today only because nothing sets a
global token.

## Decisions

- **In-memory, per session.** The token lives in Riverpod state and dies with the
  app. This matches `serverBaseUrl`, which is likewise re-entered each launch,
  adds no dependency, and writes no credential to disk. Rejected: OS credential
  store (new plugin; Linux needs libsecret; web has no backing store) and a
  plaintext config file (a live bearer readable by any process as the same user).
- **`SettingsNotifier` owns the setter**, mirroring `setServerBaseUrl`. Rejected:
  writing `authTokenProvider` straight from the widget (the form loses its echo of
  the current value and diverges from the base-URL field) and a new
  `ServerConnectionNotifier` unifying URL + token (refactors working code for no
  user-visible gain).
- **No `ApiClient` rebuild.** `apiClientProvider` passes
  `authTokenProvider: () => ref.read(authTokenProvider)`, i.e. it resolves per
  request, so updating the provider takes effect immediately.
- **WebSocket needs nothing.** `BearerAuthMiddleware.__call__` returns early for
  any scope whose `type != "http"`, so the WS upgrade is not bearer-gated; that
  channel is already covered by per-session tokens.
- **Provider API keys stay a separate credential.** They travel as `api_key` query
  params / `X-Provider-Api-Key` and must not be conflated with the server bearer.

## Components

| File | Change |
| --- | --- |
| `client/lib/data/providers/settings_state.dart` | Add `String? serverBearerToken`, its `copyWith` parameter, a `clearServerBearerToken` flag following the existing `clearError` idiom, plus `==` / `hashCode`. |
| `client/lib/data/providers/settings_notifier.dart` | `setServerBearerToken(String? token)`: update state, write `authTokenProvider`, clear `authRequiredProvider`, then `await load()` so a wrong token surfaces immediately rather than at the next job. Empty string is treated as unset. Clearing the banner here is needed because `AuthRequiredNotifier` never auto-clears on a successful request — otherwise a valid token would leave the stale banner on screen. |
| `client/lib/presentation/settings/settings_screen.dart` | Second row in the "OmniScribe Backend Connection" card: `AppInput` labelled "Bearer Token", `obscureText: true`, `monospace: true`, `showClearButton: true`, helper "Leave empty when the backend has no OMNISCRIBE_AUTH_TOKEN", and an "Apply token" button that commits the trimmed text. No reveal toggle — the field is paste-only. |
| `client/lib/core/network/api_client.dart` | Interceptor sets `Authorization` only when the request does not already carry one, so caller-supplied bearer tokens win. |
| `client/lib/presentation/common/auth_required_banner.dart` | Copy: "the API rejected the request with a 401. Check the bearer token in Settings to continue." A token can now be set *and wrong*, which "Set a bearer token" misstates. |
| `client/lib/presentation/settings/settings_screen.dart` (tab 3) | The "Security & Auth" badge read "Auth middleware deferred — settings have no effect today", which this feature makes actively wrong; replaced with a pointer to where the bearer is entered. |
| `client/README.md` | The "Bearer token rejected" troubleshooting bullet named a message the client never shows and offered no client-side remedy. Retitled to the real banner text and pointed at the new field. |

## Data flow

`AppInput` → "Apply token" → `SettingsNotifier.setServerBearerToken` →
(`SettingsState.serverBearerToken` for display) + (`AuthTokenNotifier` for
transport) → `ApiClient` interceptor reads `authTokenProvider` per request →
outgoing `Authorization: Bearer …`.

## Error handling

`load()` failures surface through the existing `SettingsState.error`. A 401 after
applying fires `authRequiredProvider` → banner, which is now the accurate
diagnosis. No retry, no token validation client-side: the server's constant-time
comparison is the validator.

## Tests

Written before the implementation:

1. `ApiClient` preserves a caller-supplied `Authorization` when a global token is
   set. Fails before the interceptor change, proving the clobber defect.
2. `setServerBearerToken` updates both `authTokenProvider` and state, clears
   `authRequiredProvider`, and `''`/null unsets both.
3. Widget test on the Settings screen: typing + "Apply token" reaches
   `authTokenProvider`, and the field renders masked.
4. Banner copy assertion in `auth_required_banner_test.dart` updated to the new
   wording.

## Deferred explicitly

- Persisting the token across launches.
- A Settings field for the server's own token *rotation*, and any UI indicating
  whether the backend currently requires one (would need a server signal such as
  `auth_enabled` on `/api/health`).
- Masking the token in `SettingsState` debug output.

## Verification

- `flutter test` (full client suite green, new tests observed failing first)
- `flutter analyze` (no issues)
- Manual, with `OMNISCRIBE_AUTH_TOKEN` armed on the backend: launch, confirm the
  banner appears, apply the correct token and confirm the banner clears, apply a
  wrong token and confirm it returns (via the failing `load()`), then run a job to
  confirm exports and artifact downloads still work.
