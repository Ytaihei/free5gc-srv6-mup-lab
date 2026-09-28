## Summary

English | [日本語](PULL_REQUEST_TEMPLATE.ja.md)

Describe the change and the lab behavior it affects.

## Verification

- [ ] `make check`
- [ ] Ansible syntax check
- [ ] Baseline test, if runtime behavior changed
- [ ] MUP test, if routing or data-plane behavior changed
- [ ] No real secrets, generated cloud-init, unreviewed captures, images, or backups added
- [ ] Any reviewed PCAP exception has packet-level review, bilingual notes and exact inventory/digest entries
- [ ] Dependency/license records updated, if applicable

## Evidence

List the commands run and attach concise, sanitized output or artifact hashes.
