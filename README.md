# Talk-to-TikTok MCP (remote)

Remote **Streamable HTTP** MCP server for the **TikTok Business (Ads) API**, built to
be added as a **Claude custom connector** and deployed on **Railway**. Same shape as
`Talk-to-Meta-SB-remote`, `Talk-to-Google-SB-remote`, and `Talk-to-Redshift-SB-remote`
— but with an **authless / Parker-style** auth model instead of the OAuth passphrase flow.

## Auth model — authless (Parker-style)

This server advertises **no OAuth**, so Claude connects with **no login step**. Access
is controlled by an optional shared token embedded in the connector URL:

```
https://<your-service>.up.railway.app/mcp?access_token=<MCP_AUTH_TOKEN>
```

- If `MCP_AUTH_TOKEN` is set, every MCP request must carry that token — via the
  `?access_token=` query param in the connector URL (Parker-style) **or** an
  `Authorization: Bearer <token>` header. Requests without it get `401`.
- If `MCP_AUTH_TOKEN` is empty, the server runs **fully open** — anyone with the
  Railway URL can use it.
- `/health` is always open (for Railway health checks).

> **Note / caveat.** Anthropic's connector UI does **not** officially support tokens in
> the connector URL — it's not read as *Claude's* auth; it works because Claude calls the
> URL you give it verbatim and *this server* validates the token. This is the same
> mechanism Parker relies on. It is undocumented and could change. If Claude ever fails
> to connect with the `?access_token=` URL, leave `MCP_AUTH_TOKEN` empty (fully open) and
> keep the Railway URL private, or switch to the OAuth flow used by the other SB remotes.
> The token embedded in a URL is a shared secret — it leaks via logs/history and rotating
> it means re-distributing the URL to everyone.

## Tools

The advertiser account is chosen per call via `advertiser_id`, or via the
`TIKTOK_ADVERTISER_ID` default, or with `tiktok_ads_switch_ad_account`.

Tools are annotated (`readOnlyHint` / write) so Claude's permission UI can bucket them.

**Read-only (15):**
`tiktok_ads_auth_status`, `tiktok_ads_switch_ad_account`, `tiktok_ads_get_campaigns`,
`tiktok_ads_get_campaign_details`, `tiktok_ads_get_adgroups`,
`tiktok_ads_get_campaign_performance`, `tiktok_ads_get_adgroup_performance`,
`tiktok_ads_get_gmv_performance`, `tiktok_ads_get_ad_creatives`, `tiktok_ads_get_custom_audiences`,
`tiktok_ads_get_targeting_options`, `tiktok_ads_get_available_metrics`,
`tiktok_ads_generate_report`, `tiktok_ads_get_report_status`,
`tiktok_ads_download_report`

`tiktok_ads_get_gmv_performance` returns payment-value ("GMV") / shopping-conversion
metrics (`complete_payment`, `total_complete_payment`, `onsite_shopping`, etc.) joined
with budget context, for standard website/shopping conversion campaigns (no GMV Max
clients today, so this intentionally excludes GMV Max fields like `roas_bid`). The
payment/shopping metric names are unverified against a live account — see the code
comment in `tools/reporting_tools.py` (`GMV_METRICS`) before relying on them in prod.

**Write (2):** `tiktok_ads_create_campaign`, `tiktok_ads_create_adgroup`

**Destructive:** none (no delete/update tools exist in the source yet).

> `generate_quick_report` exists in the source but is **not registered**: it polls up to
> 5 minutes and can exceed HTTP request timeouts. Enable it only if you accept
> long-running calls (async report generation via the three report tools above is the
> remote-safe path).

### Not exposed (and why)

Present in the source repo but intentionally **not** registered:

- `create_ad_creative`, `analyze_creative_performance`, `create_custom_audience`,
  `analyze_audience_insights` — return **hardcoded mock data** in the source. Exposing
  them would make Claude report fabricated metrics/IDs as real. Re-enable only once they
  call the real TikTok API.
- `upload_image` — reads a file from the **server's** local disk (`image_path`), which
  doesn't exist on Railway. Needs a remote-safe upload (URL or base64) first.

## Environment variables

| Var | Required | Purpose |
|-----|:--:|---------|
| `TIKTOK_ACCESS_TOKEN` | ✅ | Shared TikTok Business API token for the whole team |
| `SERVER_URL` | ✅ (prod) | Public Railway URL; pins the Host header (avoids `421 Invalid Host`) |
| `MCP_AUTH_TOKEN` | optional | Shared gate token (URL `?access_token=` / Bearer). Empty = fully open |
| `TIKTOK_ADVERTISER_ID` | optional | Default advertiser account |
| `MCP_TRANSPORT` | optional | `streamable-http` (default), `sse`, or `stdio` |
| `MCP_HOST` / `MCP_PORT` | optional | Bind address/port (Railway injects `PORT`) |

## Deploy on Railway

1. Push this repo to GitHub and create a Railway service from it (**Deploy from Dockerfile**).
2. Set env vars: `TIKTOK_ACCESS_TOKEN`, `SERVER_URL` (the service's public URL), and
   optionally `MCP_AUTH_TOKEN` and `TIKTOK_ADVERTISER_ID`.
3. Redeploy. Verify `GET https://<service>.up.railway.app/health` returns `{"status":"ok"}`.
4. In Claude → Settings → Connectors → **Add custom connector**, paste:
   `https://<service>.up.railway.app/mcp?access_token=<MCP_AUTH_TOKEN>`
   (or without the query string if running open).
   On Team/Enterprise, an **Owner** can add it org-wide so it appears for everyone.

## Local dev

```bash
pip install -e .
export TIKTOK_ACCESS_TOKEN=... TIKTOK_ADVERTISER_ID=...
python -m tiktok_ads_mcp            # Streamable HTTP on :8000
# or: MCP_TRANSPORT=stdio python -m tiktok_ads_mcp
```

## License

MIT — see [LICENSE](LICENSE).
