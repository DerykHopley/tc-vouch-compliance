---
id: sign-in-settings-record
title: Sign-in and account settings, checked October 2026
kind: implemented
version: "2026-10"
owner: IT operations lead
approval_date: 2026-10-14
review_cycle: yearly
language: en
---
# Sign-in and account settings, checked October 2026

## What was checked

On 13 October 2026 an administrator compared the live settings of the directory, the VPN and the internet-facing systems with the authentication standard and the user account standard, and tried each rule with a test account.

| Setting | Rule it carries out | Found |
|---|---|---|
| Password length and leaked-password check | Authentication standard | 12 characters at least; a known leaked password was refused |
| Second factor on email, file exchange and VPN | Authentication standard | Required; 214 of 214 users have an authenticator app or a token |
| Administrator sign-in | Authentication standard | Hardware token, only from the administration server; a sign-in from a laptop was refused |
| Lockout and screen lock | Authentication standard | Locked for 15 minutes after ten failed sign-ins; screens lock after ten minutes |
| Unusual sign-in alerts | Authentication standard | On; three alerts this quarter, each looked into by IT the same day |
| Personal accounts | User account standard | Every sign-in account belongs to one person, apart from the six shop floor display accounts on the list |
| Everyday accounts and administrator groups | Access rights policy | The directory refuses to add an everyday account to an administrator group; tried and refused |
| Manufacturer accounts | User account standard | Disabled or given a new password on the four systems that went live this quarter |
| Technical accounts | User account standard | Interactive sign-in denied; tried with two technical accounts and refused |
| Password manager | User account standard | Installed on all 238 laptops |

## Deviations

None.
