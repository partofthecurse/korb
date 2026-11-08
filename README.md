# Korb

A self-hosted shopping list with a Bring-style interface, Alexa list import, user accounts, optional two-factor authentication, and a Tailscale Funnel endpoint. Active Alexa items are saved locally before being removed from Amazon; duplicate items are combined and quantities are counted. The app is independent of CastNavidrome and uses `aioamazondevices`. Developed with assistance from GitHub Copilot. Third-party asset notices are in [NOTICE](NOTICE).

## Requirements

- Docker Compose v2 and access to `ghcr.io/partofthecurse/korb`.
- An Amazon account with an Alexa shopping list and a TOTP authenticator seed.
- A Tailscale account with Funnel enabled, MagicDNS, and a reusable auth key permitted to create the Funnel node.

## Installation

1. Copy `compose.yaml`, the complete `config/` directory, and `.env.example` to a folder on your Docker host. Copy `.env.example` to `.env` and set `SHOPPING_IMAGE=ghcr.io/partofthecurse/korb:latest`, `TS_HOSTNAME=liste`, and `TS_CERT_DOMAIN=liste.drake-aldebaran.ts.net`.
2. Save your reusable Tailscale auth key in `secrets/tailscale_authkey`; protect it and `.env` with `chmod 600`.
3. Create the first admin account before starting the public Funnel:

   ```sh
   docker compose run --rm --no-deps --entrypoint python shoppinglist -m shoppinglist.manage create-admin
   ```

4. Start the app and Funnel:

   ```sh
   docker compose up -d
   ```

5. Open <https://liste.drake-aldebaran.ts.net>, sign in, then enter your Amazon region, email, password, and TOTP seed under **Amazon**. The credentials and refresh session are encrypted in SQLite. The list syncs at startup and every five minutes; each successful import removes the saved active items from Alexa.
6. Add the page to a phone's home screen: use **Install app** in Android Chrome or **Share → Add to Home Screen** in iOS Safari.

To update after a new release, run `docker compose pull && docker compose up -d`.

## Notices

- Food icons: Twemoji by Twitter, Inc. and contributors, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); distributed via [`@twemoji/svg`](https://www.npmjs.com/package/@twemoji/svg), which is MIT-licensed.
- Fonts: DM Sans by The DM Sans Project Authors and Outfit by The Outfit Project Authors, both licensed under the [SIL Open Font License 1.1](https://openfontlicense.org/). License files are included with the fonts.
- GitHub Copilot provided AI-assisted development assistance. See [NOTICE](NOTICE) for the corresponding asset notices.
