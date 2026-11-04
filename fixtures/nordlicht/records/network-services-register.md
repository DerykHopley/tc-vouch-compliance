---
id: network-services-register
title: Register of network services for transferring information
kind: implemented
version: "7"
owner: IT operations lead
approval_date: 2026-09-08
review_cycle: yearly
language: en
---
# Register of network services for transferring information

## Services

All four services run on our own servers in the company server room.

| Service | Used for | Highest classification allowed | Protection in transit | Checked |
|---|---|---|---|---|
| Company email | Day-to-day mail | Internal; confidential only with end-to-end encryption | TLS 1.2 or newer enforced; end-to-end encryption for marked mail | 2026-09-08 |
| Customer exchange platform | Drawings and prototype data | Strictly confidential | TLS 1.3; files encrypted at rest; downloads logged | 2026-09-08 |
| VPN | Remote access | Strictly confidential | IPsec with second factor | 2026-09-08 |
| Supplier portal | Orders and delivery notes | Internal | TLS 1.2 or newer | 2026-09-08 |

## Check

On 8 September 2026 IT tested each service from outside our network with a TLS scanning tool: no service accepted a connection without encryption, and none offered a protocol older than TLS 1.2.
