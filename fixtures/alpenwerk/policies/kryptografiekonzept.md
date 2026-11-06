---
id: kryptografiekonzept
title: Kryptografiekonzept
kind: documented
version: "1.4"
owner: IT-Leiter
approval_date: 2026-09-14
review_cycle: yearly
language: de
---
# Kryptografiekonzept

## Verfahren und Schlüsselstärken

| Einsatz | Verfahren | Mindeststärke |
|---|---|---|
| Datenübertragung | TLS 1.2 oder 1.3 | 2048 Bit RSA oder 256 Bit ECC |
| Festplatten von Notebooks und Servern | AES im XTS-Modus | 256 Bit |
| E-Mail mit vertraulichem Inhalt | S/MIME | 3072 Bit RSA |
| Signaturen und Hashwerte | SHA-256 oder stärker | – |

Die Verfahren richten sich nach der aktuellen Technischen Richtlinie TR-02102 des BSI. Der IT-Leiter prüft die Tabelle jedes Jahr.

## Lebenszyklus der Schlüssel

Schlüssel werden auf dem Hardware-Sicherheitsmodul der IT erzeugt und gespeichert. Das Konzept regelt für jeden Schlüssel Archivierung, Abruf, Verteilung, Deaktivierung, Erneuerung spätestens nach drei Jahren und Löschung. Die Schlüsselverwaltung dokumentiert jeden Schritt.

## Schlüsselhoheit bei Cloud-Diensten

Vertrauliche Daten, die ein Anbieter außerhalb von Alpenwerk verarbeitet, werden mit Schlüsseln verschlüsselt, die nur Alpenwerk besitzt (kundenverwaltete Schlüssel aus unserem Hardware-Sicherheitsmodul). Der Anbieter darf keinen Zugriff auf diese Schlüssel haben. Für jeden Cloud-Dienst hält das Register der Cloud-Dienste fest, wer die Schlüssel verwaltet.
