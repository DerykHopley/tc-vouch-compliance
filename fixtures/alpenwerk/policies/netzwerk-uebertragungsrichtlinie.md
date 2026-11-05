---
id: netzwerk-uebertragungsrichtlinie
title: Richtlinie für Netzwerke und Datenübertragung
kind: documented
version: "2.0"
owner: IT-Leiter
approval_date: 2026-09-14
review_cycle: yearly
language: de
---
# Richtlinie für Netzwerke und Datenübertragung

## Netzwerkdienste

Alle Netzwerkdienste, über die Informationen übertragen werden, stehen im Verzeichnis der Netzwerkdienste. Ein neuer Dienst wird erst genutzt, wenn die IT ihn dort eingetragen und freigegeben hat.

## Nutzung nach Klassifizierung

| Klassifizierung | Erlaubte Übertragung |
|---|---|
| Öffentlich | jeder freigegebene Dienst |
| Intern | jeder freigegebene Dienst mit Transportverschlüsselung |
| Vertraulich | nur der CAD-Datenaustausch oder E-Mail mit S/MIME |
| Streng vertraulich | nur nach Freigabe durch die Informationssicherheitsbeauftragte, mit Inhaltsverschlüsselung |

## Schutz der Übertragung

Alle Informationen werden mindestens transportverschlüsselt übertragen (TLS 1.2 oder höher). Unverschlüsselte Protokolle wie FTP oder Telnet sind an der Firewall gesperrt. Vertrauliche Zeichnungen werden zusätzlich inhaltsverschlüsselt.

## Richtige Adressierung

Das E-Mail-Programm warnt vor dem Versand an externe Empfänger. Vertrauliche Zeichnungen werden nur an Empfänger gesendet, die im CAD-Datenaustausch für das jeweilige Projekt freigeschaltet sind.

## Fernzugriff

Mitarbeiter und Dienstleister greifen nur über das VPN auf das Unternehmensnetzwerk zu. Das VPN verlangt Mehrfaktor-Authentifizierung und ist mit TLS 1.3 verschlüsselt. Ein VPN-Zugang wird über das Verfahren zur Verwaltung von Benutzerkonten beantragt, genehmigt und am Ende gesperrt. Fernwartung durch Maschinenhersteller läuft nur über das VPN und wird von der IT für jede Sitzung freigeschaltet.
