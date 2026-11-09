---
id: backup-policy
title: Backup policy
kind: documented
version: "1.0"
owner: IT operations lead
approval_date: 2024-02-12
review_cycle: yearly
language: en
---
# Backup policy

## What is backed up

| IT service | Backup | Recovery point (RPO) | Recovery time (RTO) | Restored after |
|---|---|---|---|---|
| Directory and sign-in | Nightly | 24 hours | 4 hours | – |
| File server | Nightly | 24 hours | 8 hours | Directory and sign-in |
| ERP system | Every 4 hours | 4 hours | 8 hours | Directory and sign-in |
| Email | Nightly | 24 hours | 24 hours | Directory and sign-in |

Services are restored in the order of this table.

## How backups are protected

Backups are encrypted, written to a backup server in the second fire section of the building, and copied each week to a storage system at an outside data centre. Only the backup administrators can delete them.

## Recovery

Each service has a recovery guide that says what is needed to restore it: the people, the hardware and the time. Restores run one service at a time, so the backup server and the network are not overloaded.

## Restore tests

Once a year IT restores a sample of files and one whole server to a test system, to show that restores work.

## Review

IT reviews this policy and the recovery guides every year.
