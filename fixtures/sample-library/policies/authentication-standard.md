---
id: authentication-standard
title: Authentication standard
kind: documented
version: "1.3"
owner: IT operations lead
approval_date: 2026-03-09
review_cycle: two-yearly
language: en
---
# Authentication standard

## How we choose a login method

For each system, IT and the information security officer weigh the risk before deciding how users sign in: what information the system holds, who uses it, and whether it can be reached from the internet. The result is recorded in the system's entry in the IT system list.

## The rules that follow from it

The login methods follow recognised good practice, such as the national guidance on secure passwords and multi-factor sign-in.

- Every user signs in with a personal password of at least 12 characters, checked against lists of known leaked passwords.
- Every system reachable from the internet needs a second factor: an authenticator app or a hardware token.
- Administrator accounts always sign in with a hardware token, through the administration server.

## Extra protection

After ten failed logins an account is locked for 15 minutes. Screens lock after ten minutes without use, and sessions on internet-facing systems end after eight hours. Sign-ins from unusual countries or at unusual times raise an alert to IT.
