# Security policy

English | [日本語](SECURITY.ja.md)

## Scope

This project creates an isolated research lab. It is not a production 5G core,
security boundary, or internet-facing service. The example PLMN, subscriber
identity, permanent key, OPC, and free5GC WebUI credentials are intentionally
public lab values and must never be reused outside an isolated test network.

Only the latest revision of the default branch is supported. Historical lab
snapshots and files explicitly marked legacy are retained for research context
and do not receive security fixes.

## Reporting a vulnerability

Use GitHub private vulnerability reporting after it is enabled for the
repository. Until then, contact the repository owner privately. Do not place a
working exploit, real credential, private key, Tailnet configuration, or data
captured from a non-lab subscriber in a public issue.

Include the affected commit, component, reproduction conditions, and expected
security property. Reports involving upstream software should also identify
the upstream project and version.

## Deployment expectations

- Keep all lab user-plane networks isolated from the physical LAN.
- Expose the dashboard only on loopback or an authenticated private overlay.
- Use dedicated lab credentials and SSH keys.
- Review generated cloud-init files before sharing diagnostics.
- Do not publish VM images or container archives without a secret and license
  audit.
