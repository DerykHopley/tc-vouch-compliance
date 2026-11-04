---
id: encryption-standard
title: Encryption standard
kind: documented
version: "1.2"
owner: Information security officer
approval_date: 2022-03-14
review_cycle: yearly
language: en
---
# Encryption standard

## Laptops

Every company laptop uses full-disk encryption with AES-256. IT turns it on before a laptop is handed out, and users cannot turn it off.

## Connections

Connections to our web services and email servers use TLS 1.2 or newer. Older protocol versions are switched off.

## Approved methods

We use only encryption methods that the national cyber security authority recommends: AES with keys of at least 128 bits for stored data, RSA with at least 3072 bits or elliptic curves with at least 256 bits for key exchange and signatures, and SHA-256 or stronger for hashing.

## Keys

Encryption keys are created in the device management system or on the server that uses them. Recovery keys for laptops are stored in the central device management system; users do not keep their own copy. Keys are withdrawn when a device is retired and renewed when a certificate expires.

## Monthly check

IT checks the encryption status of every laptop each month, using the device management report. A laptop found unencrypted is locked until it is fixed.
