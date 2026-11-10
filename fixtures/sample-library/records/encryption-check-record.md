---
id: encryption-check-record
title: Encryption check, September 2026
kind: implemented
version: "2026-09"
owner: Information security officer
approval_date: 2026-09-29
review_cycle: yearly
language: en
---
# Encryption check, September 2026

## Connections

An outside scan of every internet-facing service on 22 September 2026:

| Service | Protocols offered | Result |
|---|---|---|
| Mail gateway | TLS 1.2 and 1.3 | Passed; 99.6 percent of outgoing mail in September left over TLS |
| Secure file exchange | TLS 1.3 | Passed |
| VPN | Encrypted tunnel, second factor required | Passed |
| Old test web server | TLS 1.0 | Failed; the old protocol was switched off on 24 September and the server passed a rescan |

## Stored data and keys

All 238 laptops have their disks encrypted with AES-256. The key vault holds 41 keys; none is older than two years, and two were renewed this month.

## Files sent outside

A sample of 50 shares on the file exchange in September: each went to one named person and ended after 14 days at the latest. Of 30 emails with confidential attachments, all had the attachment encrypted.

## Remote access

Two remote maintenance sessions for suppliers were opened by IT for their jobs and closed the same day. Every VPN account in use has a second factor.
