# ChatGPT-plan connection pilot

English | [日本語](issue-chatgpt-pilot.ja.md)

This manual pilot tests the official OSS Sign in with ChatGPT route before
validating the Issue reviewer's subscription connection. This smoke CLI is **not connected to the coordinator**:
no Issue, Qwen job, code review, PR, merge, timer or lab operation is started.
Keep the coordinator disabled; it no longer accepts a Platform API key.

## Prerequisites and spending boundary

Use Python 3.10+ with PyJWT and cryptography already installed. They are optional
pilot dependencies, not normal development requirements. Missing dependencies
block login; nothing is installed automatically. Authenticate your own eligible
ChatGPT account. Do not copy existing Codex auth, share tokens, or run as root.

Login does not perform inference. Before `smoke`, inspect ChatGPT Settings → Usage
for this app. Verify that app credit consumption and automatic credit purchases
are disabled. **If that boundary cannot be established, stop without passing the
confirmation flag.** The flag is an operator attestation, not an API-enforced
spending cap. The pilot cannot read/change those settings. OAuth alone does not
guarantee included-only usage: account settings can permit credit consumption.

There is no Platform API-key loading, purchase, model fallback or automatic
inference retry. A failed call consumes the single pilot attempt. Client time and
size limits do not guarantee a server-side token/cost limit.

## Login and test

On the Linux host, from this trusted checkout (no sudo):

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" login
```

It listens only on `127.0.0.1:1465` for at most ten minutes. Open the printed
official URL and approve the intended account/app. If using another computer's
browser, first open an SSH tunnel from that computer (replace the example host):

```bash
ssh -N -L 1465:127.0.0.1:1465 operator@linux-host
```

Keep both commands running until the login terminal confirms validation. Do not
expose the callback with Tailscale Serve or bind it to all interfaces. Do not share
callback URLs or private state. The directory is `0700`, files are `0600`.
PKCE, state, RS256 signature, issuer, audience, expiry, nonce and plan permission
are checked. The issued client and stable host ID are retained for later login.

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" models
```

Choose an exact `slug` from this account's catalog. Only after verifying the
spending boundary above, run the following with that slug:

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" smoke \
  --model ACCOUNT_MODEL_SLUG --confirm-included-only
```

The only prompt asks for `MUP_PILOT_OK`; no source or tools are sent. Success needs
a successful `response.completed` and matching text. Partial streams, quota/auth
errors and malformed responses fail. Array input, `store:false`, `stream:true`
are used; unsupported `max_output_tokens` is omitted. A receipt is reserved before
sending and retained on failure. Do not delete receipts to force unreviewed retries.
Completed replies are also retained privately in `smoke-response.json`, including
on a text mismatch. Terminal and streamed text, completion/match status and time are retained,
not authentication data or HTTP headers. This does not recover replies from older
pilot versions. Text deltas are accepted only after a successful completion event;
terminal text, when also present, must agree. Mismatches still fail.

After inspecting a failed attempt and explicitly authorizing one retry, use
`retry-smoke` instead of `smoke` with the same flags/model. It preserves the first
attempt and writes `smoke-retry.json` and `smoke-retry-response.json` separately.
Only one retry is allowed; success, another model or an existing retry receipt
blocks it. This never enables automatic retries.

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" status
python3 -m unittest discover -s tests -p test_issue_chatgpt_pilot.py -v
```

Status reports file presence, not auth validity. Offline tests use synthetic
tokens/streams, not live account access. Crypto tests explicitly skip if optional
login dependencies are absent. One account registration is supported per state
directory. Automatic refresh, account switching and a logout CLI are not yet
implemented. Repeat login with the retained registration if access expires;
disconnect the app in ChatGPT settings when finished.

## Remaining integration

The [Issue reviewer](issue-development.md) now provides locked token renewal,
SHA-bound structured review, quota waiting/recovery, daily limits and regression
tests using this dedicated OAuth session. This smoke CLI itself still does not
refresh tokens. Validate `review-local`, then test an authorized Issue through Qwen, isolated
checks, review and PR/CI before considering automatic merge. Smoke success does
not attest any of those stages. Legacy timers remain off.

Official references:

- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and usage settings](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
