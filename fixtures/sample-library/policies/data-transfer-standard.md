---
id: data-transfer-standard
title: Data transfer standard
kind: documented
version: "1.4"
owner: IT operations lead
approval_date: 2026-04-27
review_cycle: two-yearly
language: en
---
# Data transfer standard

## Network services we use

| Network service | What it carries | Protection |
|---|---|---|
| Email | Day-to-day business mail | TLS between mail servers; confidential content only in encrypted attachments |
| Secure file exchange | Drawings and data for customers and suppliers | HTTPS, a personal login per external user |
| VPN | Remote work and remote maintenance | Encrypted tunnel, sign-in with a second factor |
| Internet line | Everything above, plus browsing | Firewall, only the ports these services need |

IT keeps this table current and adds a service before it is used.

## Rules by classification

- Public information may be sent by any channel.
- Internal information goes by email or the file exchange.
- Confidential information goes only through the file exchange, or by email in an encrypted attachment with the password sent another way.

## Getting it to the right person

Before sending confidential information, the sender checks the recipient's address against the contract or the order. The file exchange sends each share to one named person and ends it after 14 days.

## Encryption

All information that leaves our network is encrypted in transit at least by the connection; confidential information is also encrypted as a file.

## Remote access

Remote access to our network is possible only through the VPN, with a personal account and a second factor. IT grants it on the manager's request and removes it when the person leaves or no longer needs it. Suppliers' remote maintenance sessions are opened by IT for each job and closed afterwards.
