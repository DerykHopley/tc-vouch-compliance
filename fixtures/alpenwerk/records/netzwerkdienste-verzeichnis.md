---
id: netzwerkdienste-verzeichnis
title: Verzeichnis der Netzwerkdienste
kind: implemented
version: "2026-09"
owner: IT-Leiter
approval_date: 2026-09-21
review_cycle: yearly
language: de
---
# Verzeichnis der Netzwerkdienste

## Freigegebene Dienste

| Dienst | Zweck | Höchste Klassifizierung | Verschlüsselung | Freigegeben |
|---|---|---|---|---|
| Internetzugang | Web und Cloud-Dienste | Intern | TLS 1.2 oder höher, unverschlüsselte Protokolle gesperrt | 2024-03-04 |
| VPN | Fernzugriff von Mitarbeitern und Dienstleistern | Vertraulich | TLS 1.3, Mehrfaktor-Authentifizierung | 2025-11-10 |
| E-Mail | Kommunikation | Vertraulich mit S/MIME | TLS zwischen Mailservern erzwungen, S/MIME für vertrauliche Inhalte | 2024-03-04 |
| CAD-Datenaustausch | Zeichnungen mit Kunden | Vertraulich | TLS 1.3 und Inhaltsverschlüsselung | 2025-05-19 |
| Fernwartung | Wartung der Fertigungsmaschinen durch Hersteller | Intern | über das VPN, je Sitzung freigeschaltet | 2025-11-10 |

## Prüfung

Am 21. September 2026 hat der IT-Leiter mit einer Auswertung der Firewall bestätigt, dass nur diese Dienste genutzt werden und alle Verbindungen verschlüsselt sind.

## VPN-Zugänge

Ende September 2026 hatten 41 Mitarbeiter und 4 Dienstleister einen VPN-Zugang; jeder war über das IT-Serviceportal beantragt und genehmigt.
