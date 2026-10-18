# Proposal: project site at `gridcast.muhammadmemon.ca` (ADR-013, pending owner approval)

Status: **idea saved, nothing built or deployed.** The owner asked to be consulted before anything starts (2026-10-17). Resume from here in the next session.

## Owner answers so far
- GitHub log and site: **"Yes, both"**. The public `GridCast-forecasts` repo is the tamper-proof daily record, and the site reads from it. Still to do: the owner creates the empty public repo and adds the server deploy key with write access (key in ADR-005).
- Address: the owner asked which option is free. **Both are free**: Cloudflare Workers static assets, same setup as muhammadmemon.ca, on a domain the owner already owns. Recommendation: the subdomain `gridcast.muhammadmemon.ca`, which keeps the main site's repo and deploys untouched. The owner still needs to confirm.
- Building a preview: **pending** (owner to restart the session).

## Pages
1. Tomorrow's forecast: fan chart (21 quantiles × 24 hours), issue time, link to the day's commit.
2. Track record: forecast vs actual, rolling CRPS/MAE/coverage, days-live counter.
3. The research: question, pre-registration and its hash, pipeline diagram, model comparison (validation now; test results after G1/G2), RQ1 regime analysis and placebo check.
4. Battery simulations (after M5).
5. Data & methods: sources and attribution, leakage protection, limitations, "not trading advice".

## Architecture
- Static site in `site/` in the GridCast repo; charts come from results files (plan §0 rule 6). Deployed with `wrangler` as a separate Workers project on the custom domain.
- Daily data: the server pushes `latest.json` + `scorecard.json` to `GridCast-forecasts`; the site fetches them from raw.githubusercontent.com (CORS-enabled). No Cloudflare credentials on the server.
- Replaces the plan's Streamlit dashboard (M6-T7).

## Constraints
- Grid Status terms: no raw historical price series from the Grid Status period; attribution with link and logo. Actual-price charts use our own IESO collection (July 2026 onward); older periods only as aggregates.
- IESO terms of use: `[VERIFY]` before launch.
- Test-window results appear only after G1 (ADR-011 seal).
- A link from muhammadmemon.ca to the subdomain is a separate change, needing approval.

## Process
Build locally, then a private preview for the owner; deploy only on approval. Target: launch with the first public forecast (~2026-11-01).
