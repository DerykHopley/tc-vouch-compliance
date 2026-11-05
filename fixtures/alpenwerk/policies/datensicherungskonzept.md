---
id: datensicherungskonzept
title: Datensicherungs- und Wiederherstellungskonzept
kind: documented
version: "3.0"
owner: IT-Leiter
approval_date: 2026-10-05
review_cycle: yearly
language: de
---
# Datensicherungs- und Wiederherstellungskonzept

## Maßgebliche IT-Dienste

| Dienst | Sicherung | RPO | RTO | Wiederherstellung nach |
|---|---|---|---|---|
| Verzeichnisdienst | stündlich, Snapshot | 1 Stunde | 4 Stunden | – (zuerst) |
| ERP | stündlich, Datenbank und Dateien | 1 Stunde | 8 Stunden | Verzeichnisdienst |
| Maschinenprogramme (DNC) | täglich | 24 Stunden | 12 Stunden | Verzeichnisdienst, ERP |
| CAD-Tresor | täglich, vollständig | 24 Stunden | 24 Stunden | Verzeichnisdienst |
| Dateiserver | täglich, inkrementell | 24 Stunden | 48 Stunden | Verzeichnisdienst |

Die Tabelle steht in der Reihenfolge der Wiederherstellung: jeder Dienst kommt nach den Diensten in der letzten Spalte, von denen er abhängt, und nach der Wiederherstellungszeit (RTO).

## Schutz der Sicherungen

Die Sicherungen werden verschlüsselt (AES 256 Bit) auf ein Speichersystem in einem zweiten Brandabschnitt geschrieben und wöchentlich auf Bänder ausgelagert, die in einem Bankschließfach in Kufstein liegen. Das Speichersystem ist unveränderlich: Sicherungen können 30 Tage lang nicht gelöscht oder überschrieben werden. Nur zwei Administratoren haben Zugriff.

## Ressourcen für die Wiederherstellung

Für eine vollständige Wiederherstellung stehen ein Ersatzserver und zwei Administratoren mit Vertretung bereit. Damit das Speichersystem bei einer vollständigen Wiederherstellung nicht überlastet wird, werden die Dienste nacheinander in der Reihenfolge der Tabelle wiederhergestellt.

## Wiederherstellungstests

Jeder maßgebliche Dienst wird mindestens einmal jährlich testweise auf einem Testsystem wiederhergestellt. Das Ergebnis wird im Protokoll der Wiederherstellungstests festgehalten.

## Überprüfung des Konzepts

Der IT-Leiter prüft dieses Konzept jedes Jahr methodisch anhand der Ergebnisse der Wiederherstellungstests und der Änderungen in der IT. Die letzte Prüfung erfolgte im Oktober 2026.
