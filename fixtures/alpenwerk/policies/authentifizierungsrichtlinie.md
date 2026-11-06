---
id: authentifizierungsrichtlinie
title: Authentifizierungsrichtlinie
kind: documented
version: "3.0"
owner: IT-Leiter
approval_date: 2026-09-14
review_cycle: yearly
language: de
---
# Authentifizierungsrichtlinie

## Grundlage

Die Anmeldeverfahren dieser Richtlinie folgen aus der Risikobewertung der Anmeldeverfahren, die der IT-Leiter jedes Jahr erstellt. Sie berücksichtigt, ob ein System direkt aus dem Internet erreichbar ist, welche Daten es enthält und welche Angriffe zu erwarten sind, zum Beispiel Phishing und das Durchprobieren von Passwörtern. Die Verfahren richten sich nach den Empfehlungen des BSI-Grundschutzes und des NIST zu Passwörtern.

## Passwörter

- Passwörter haben mindestens 14 Zeichen.
- Passwörter werden nur bei Verdacht auf Kompromittierung geändert, dann aber sofort.
- Die Anmeldedaten werden gegen eine Liste bekannter und geleakter Passwörter geprüft. Der Verzeichnisdienst lehnt Standard-Passwörter und solche aus der Liste technisch ab.

## Mehrfaktor-Authentifizierung

Eine zweite Anmeldung mit einem Hardware-Token ist Pflicht für alle Administratorkonten, für den Fernzugriff über VPN und für alle Cloud-Dienste. Ein Token ist persönlich, wird nicht weitergegeben und bei Verlust sofort beim Security-Desk gesperrt.

## Ergänzende Maßnahmen

- Nach fünf Fehlversuchen wird ein Konto für 15 Minuten gesperrt.
- Bildschirme sperren sich nach 10 Minuten ohne Eingabe, Fertigungsterminals nach 30 Minuten.
- VPN-Sitzungen werden nach 8 Stunden beendet.
- Anmeldungen an VPN und Cloud-Diensten aus ungewöhnlichen Ländern lösen eine Warnung an den Security-Desk aus.

## Umgang mit Anmeldeinformationen

- Anmeldedaten werden an niemanden weitergegeben, auch nicht an Vorgesetzte.
- Passwörter werden nicht notiert und nur im freigegebenen Passwort-Manager gespeichert.
- Bei Verdacht auf Kompromittierung wird das Passwort sofort geändert und der Security-Desk informiert.
