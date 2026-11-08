# Korb

Gemeinsame Einkaufsliste mit Alexa-Import, Benutzerverwaltung, optionalem TOTP und Tailscale Funnel. Der Dienst ist unabhängig von CastNavidrome. Die Alexa-Integration verwendet ausschließlich `aioamazondevices` und eine eigene Sitzung.

## Voraussetzungen

- Docker Compose, ein Amazon-Konto mit Alexa-Einkaufsliste und TOTP-Anmeldung.
- Ein Tailnet mit aktiviertem MagicDNS, HTTPS und Funnel sowie ein Tailscale-Auth-Key für den Sidecar. Funnel ist öffentlich; der Zugriff auf die Anwendung erfordert ein eigenes Konto.
- Ein veröffentlichtes Anwendungsimage unter `ghcr.io/partofthecurse/korb` sowie ein Tailscale-Auth-Key mit Funnel-Berechtigung.

## Einrichtung mit Image

1. `compose.yaml`, den vollständigen Ordner `config/` und `.env.example` in ein eigenes Verzeichnis kopieren.
2. `.env.example` als `.env` anlegen und `SHOPPING_IMAGE`, den Tailscale-Auth-Key-Dateipfad sowie den vollständigen `TS_CERT_DOMAIN` eintragen. Den Tailscale-Auth-Key als Datei ablegen und mit `chmod 600` schützen; `.env` ebenfalls entsprechend schützen. Amazon-Zugangsdaten werden später im angemeldeten Adminbereich unter **Amazon** eingegeben. Der Tailscale-Sidecar liest seinen Auth-Key zur Laufzeit aus einem Docker-Secret.
3. Einmalig das erste Konto anlegen:

   ```sh
   docker compose run --rm --no-deps --entrypoint python shoppinglist -m shoppinglist.manage create-admin
   ```

4. `docker compose up -d` starten. Die Seite ist unter `https://${TS_CERT_DOMAIN}/` erreichbar. Der Sidecar muss im Tailnet autorisiert sein und Funnel muss in der Tailnet-Richtlinie erlaubt sein. Die Anwendung selbst veröffentlicht keinen Host-Port.
5. Unter **Amazon** zuerst die Amazon-Region auswählen, dann E-Mail, Passwort und Amazon-TOTP-Geheimnis eintragen. Die Werte werden verschlüsselt in SQLite gespeichert, nach erneutem Passwort und optionalem App-2FA-Code. Sie werden danach nicht wieder angezeigt. Unter **Benutzer** weitere Konten anlegen; unter **Konto** lässt sich App-TOTP pro Benutzer aktivieren. Wiederherstellungscodes werden einmalig angezeigt.

Beim Start und danach standardmäßig alle 300 Sekunden werden aktive Alexa-Artikel übernommen und nach Speicherung einzeln bei Amazon gelöscht. Dies gilt auch für bereits vorhandene Alexa-Artikel. `SYNC_INTERVAL_SECONDS` ändert das Intervall (mindestens 60 Sekunden). Neue Artikel können außerdem lokal oder per Alexa angelegt werden; lokale Änderungen werden nicht zurückgeschrieben.

## Lokal testen

Für eine lokale Vorschau ohne Amazon-Zugriff und Funnel:

```sh
docker compose -f compose.local.yaml up --build -d
docker compose -f compose.local.yaml exec shoppinglist python -m shoppinglist.manage create-admin
```

Die lokale Vorschau ist unter `http://<SHOPPING_BIND_ADDRESS>:32768/` erreichbar. Die Standardeinstellung bindet nur an `127.0.0.1`; für Zugriff im Heimnetz `SHOPPING_BIND_ADDRESS` in `.env` auf die feste LAN-Adresse des eigenen Docker-Hosts setzen. Der Amazon-Import startet direkt und wiederholt sich standardmäßig alle 300 Sekunden; `SYNC_INTERVAL_SECONDS` ändert das Intervall, `ENABLE_SCHEDULER=false` schaltet ihn ab. Jeder erfolgreiche Import speichert aktive Artikel zuerst lokal und löscht sie danach einzeln bei Amazon. Die lokale Vorschau verwendet HTTP; nur in einem vertrauenswürdigen Heimnetz einsetzen. Das lokale Datenvolume lässt sich über `SHOPPING_DATA_VOLUME` umbenennen.

## Smartphone-App

Die Web-App enthält ein PWA-Manifest und Korb-Icons. Sobald sie über eine HTTPS-Adresse wie den Tailscale Funnel läuft, in Android Chrome **⋮ → App installieren** oder **Zum Startbildschirm hinzufügen** wählen. Auf iPhone/iPad in Safari **Teilen → Zum Home-Bildschirm** verwenden. Die LAN-Vorschau über HTTP ist nicht installierbar. Eine APK ist für die private Nutzung nicht nötig; eine native APK würde zusätzlich einen signierten Android-Wrapper und eine veröffentlichte HTTPS-Domain erfordern.

Für Entwicklung mit aktivem Amazon-Import `.env` mit Zugangsdaten anlegen und die Produktions-Compose-Datei verwenden; das Tailscale-Sidecar setzt dabei zusätzlich einen Tailnet-Hostnamen und Auth-Key voraus.

Für Tests ohne Amazon-Zugriff:

```sh
docker build -t shoppinglist:test .
docker run --rm --network none -v "$PWD/tests:/app/tests:ro" --tmpfs /tmp -e SHOPPING_DATA_DIR=/tmp/data --entrypoint python shoppinglist:test -m unittest discover -s /app/tests -v
```

## Betrieb und Wiederherstellung

- Das Volume `shopping-data` enthält Datenbank, Amazon-Sitzung sowie Sitzungsschlüssel und getrennte Schlüsseldateien für App-2FA und Amazon-Zugangsdaten. Amazon-Passwort, TOTP-Seed und Refresh-Token sind in SQLite verschlüsselt; Schlüsseldateien liegen daneben mit restriktiven Rechten. Das Volume vertraulich behandeln und verschlüsselte Backups erstellen. `tailscale-state` separat sichern. SQLite konsistent sichern, z. B. bei gestopptem Dienst.
- Bei ausgesperrten Admins lokal `docker compose run --rm --entrypoint python shoppinglist -m shoppinglist.manage reset-2fa USERNAME` oder `reset-password USERNAME` verwenden. Beide Befehle widerrufen bestehende Sitzungen des Kontos.
- Amazon kann CAPTCHA oder andere Anmeldeprüfungen verlangen. In diesem Fall zeigt die Oberfläche einen Synchronisierungsfehler; es werden keine nicht gespeicherten Einträge gelöscht. Amazon verwendet keine offiziell garantierte Schnittstelle, die sich ändern kann. Ältere Compose-Installationen können vorübergehend weiter `AMAZON_EMAIL`, `AMAZON_PASSWORD` und `AMAZON_TOTP_SECRET` als Umgebungsvariablen nutzen; über das Web gespeicherte Werte haben Vorrang.
- Pro Abruf verarbeitet die verwendete Bibliothek bis zu 100 Einträge. Sind mehr Einträge vorhanden, meldet der Dienst eine unvollständige Antwort und versucht es erneut. Abgehakte Amazon-Einträge können den Fortschritt bei sehr großen Listen verhindern; diese gegebenenfalls in Alexa entfernen. Das Zeitlimit, Netzwerkfehler und fehlende Berechtigungen können den Import verzögern.
- Zum sicheren Betrieb regelmäßig Updates installieren, Backups prüfen, Funnel-Zugang überwachen und nur vertrauenswürdigen Personen Zugriff auf Docker und die Umgebungsdatei geben.

## Veröffentlichung

Ein veröffentlichtes GitHub Release baut und testet das Image und veröffentlicht Multi-Arch-Tags `latest`, Haupt-, Neben- und Patchversion nach GHCR. Das Docker-Build schließt `.env*`, `secrets/`, Tests, Datenbanken und GitHub-Workflowdateien aus; das Laufzeitimage kopiert zusätzlich nur `requirements.txt` und `shoppinglist/`. Beim ersten Release das GHCR-Paket einmalig auf **Public** stellen, damit andere ohne GitHub-Anmeldung `docker pull ghcr.io/partofthecurse/korb:latest` ausführen können.

## Bildnachweis

Lebensmittelicons: [Twemoji](https://github.com/twitter/twemoji), Copyright Twitter, Inc. und weitere Mitwirkende, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); ausgeliefert über [@twemoji/svg](https://www.npmjs.com/package/@twemoji/svg) unter dessen beiliegender MIT-Lizenz. Die Originalgrafiken werden unverändert verwendet.

Schriften: DM Sans von The DM Sans Project Authors und Outfit von The Outfit Project Authors, beide unter der [SIL Open Font License 1.1](https://openfontlicense.org/); Lizenztexte liegen bei den lokal eingebetteten Schriftdateien.