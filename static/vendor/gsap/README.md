# GSAP (vendored)

Self-hosted so the app never makes a third-party request and never breaks
because a CDN is unreachable.

| Field | Value |
| --- | --- |
| Package | `gsap` |
| Version | 3.15.0 |
| File | `gsap.min.js` (core only, no plugins) |
| Source | https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js |
| SHA-256 | `92BB9A96476F983D212A2BC4F54C889039C1696DD4461D40A736860938570FBB` |
| Downloaded | 2026-10-31 |
| License | Standard "no charge" license — https://gsap.com/standard-license |

## Upgrading

1. Download the new `gsap.min.js` from jsDelivr for the target version.
2. Compute `Get-FileHash gsap.min.js -Algorithm SHA256`.
3. Replace the file, update the version and hash in this table.
4. Run `manage.py test` and check the dashboard at 320px and 390px.

## ScrollTrigger

The matching GSAP 3.15.0 ScrollTrigger plugin is also self-hosted.
- Source: https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/ScrollTrigger.min.js
- SHA-256: B0B14D67B55B0C43C756AC0B106CFCB09D0879945F6EAD64451065B0672916A2
- Downloaded: 2026-10-31
- License: same GSAP standard license linked above.
