---
id: logging-standard
title: Logging standard
kind: documented
version: "2.1"
owner: IT operations lead
approval_date: 2026-09-14
review_cycle: yearly
language: en
---
# Logging standard

## Which systems log

IT assesses each system on the IT system list for whether it needs logging, and records the answer there. Servers, firewalls, the directory, the VPN and every system holding confidential information log security events.

## What is logged

Sign-ins and failed sign-ins, changes to accounts and rights, everything administrators do, and changes to security settings. The VPN logs when each remote session, including remote maintenance, starts and ends.

## Where logs are kept

Logs are sent to a central log server that only two administrators can manage, and that nobody can change entries on. They are kept for twelve months, unless a customer contract asks for longer, and are read only for security and operations, as agreed with the works council.

## Outside services

Before an outside IT service is used, IT finds out what logs and monitoring the provider offers and decides which of them to use.

## Network monitoring

The firewall and the intrusion detection system watch traffic between our network zones and to the internet, and record connections that break the firewall rules.

## Checking the logs

The central log server raises alerts for patterns such as many failed sign-ins. Each week an administrator checks the alerts and a sample of the logs for breaches of policy and anything unusual. Alerts that point to a security incident go to the incident team, which handles them under the incident management procedure.

## Contract requirements

Requirements that customer contracts set for logs, such as how long to keep them or who may read them, are listed with the contract and built into the settings of the log server.
