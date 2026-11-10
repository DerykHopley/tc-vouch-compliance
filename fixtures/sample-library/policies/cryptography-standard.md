---
id: cryptography-standard
title: Cryptography standard
kind: documented
version: "1.0"
owner: Information security officer
approval_date: 2025-10-13
review_cycle: two-yearly
language: en
---
# Cryptography standard

## Methods allowed

We use only methods that current national technical guidelines recommend:

| Use | Method | Minimum strength |
|---|---|---|
| Encrypting stored data | AES | 256-bit keys |
| Encrypting connections | TLS 1.2 or 1.3 | ciphers from the recommended list |
| Signatures | RSA or ECDSA | 3072-bit RSA or 256-bit curves |
| Hashes | SHA-2 or SHA-3 | 256 bits |

Older methods such as SHA-1, DES and TLS 1.0 are switched off wherever we find them.

## Key lifecycle

Keys are generated on the systems that use them or in the key vault, never on a personal computer. They are stored in the key vault, backed up there in encrypted form, and handed out only to the services that need them. A key is renewed at least every two years, withdrawn at once when it may have been exposed, and deleted when the data it protects is gone.

## Keys for outside services

Where a service provider processes our confidential information, we keep the keys that protect it ourselves, in our key vault, unless the information security officer approves an exception in writing.
