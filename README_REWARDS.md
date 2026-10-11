# ThreadLoop rewards prototype

## What was added

- `/rewards` page with an Eco Wallet, available offers, and redemption history.
- `GET /api/rewards/{user_id}` to return the point balance, confirmed handoff count, offers, and coupons.
- `POST /api/rewards/{user_id}/redeem/{offer_id}` to redeem an offer, generate a unique demo coupon, and deduct points through the redemption ledger.
- A `user_id` field on handoffs so confirmed handoffs can be associated with a demo wallet.
- Navigation links to Rewards from the home and garment passport pages.

## Run locally

Install dependencies and start the FastAPI server:

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/` and use the Rewards link. The first visit creates a demo user ID in that browser's local storage. Use the same browser to submit handoffs and view its wallet.

## Testing points

Points are only added when a handoff is confirmed by the existing partner-confirmation endpoint. Configure a secret token before testing confirmation:

```bash
# Linux/macOS
export PARTNER_CONFIRM_TOKEN='replace-with-a-long-random-secret'

# Windows PowerShell
$env:PARTNER_CONFIRM_TOKEN = 'replace-with-a-long-random-secret'
```

Create a handoff from a garment page, then use the partner-confirmation endpoint with the configured `X-Partner-Token` header. Each confirmed handoff awards 10 points in this prototype. The sample offers cost 50, 100, and 150 points; edit `REWARD_OFFERS` in `main.py` to change them.

## Important limitations before production

This is a demo implementation, not a production loyalty wallet. The user ID is an anonymous browser-generated identifier, not authentication; users can change it or clear browser storage. The offer catalogue and coupon codes are illustrative and are not connected to real stores. Before real use, add authentication, authorization checks, atomic/locked point-ledger transactions, partner coupon validation and redemption rules, rate limits, and appropriate privacy and fraud controls. Do not treat client-provided user IDs as proof of identity.


## Apply an issued coupon

The Rewards page includes a **Redeem a coupon** form. It accepts only a coupon code previously issued to the same demo wallet, rejects unknown codes and coupons already marked as used, and records the usage timestamp in the database. Coupon usage is shown in the coupon history. This prototype action marks a code as used; it does not contact a real store or apply a real-world discount.

For production, coupon-use requests should be authenticated by the participating store or a trusted server-side partner integration. The demo wallet ID stored in browser local storage is not proof of identity.


## HTTPS / secure hosting

### Why the local address uses HTTP
When running on your own computer with `uvicorn main:app --reload`, the address
`http://127.0.0.1:8000` is a local development address. It is not a public website,
and HTTPS is normally configured by a hosting platform or reverse proxy rather than
by this local development command.

### Deploy with HTTPS using Render
1. Push this project folder to a GitHub repository.
2. In Render, choose **New + → Blueprint** (or create a **Web Service**) and connect
   the repository containing `main.py`.
3. Use these settings:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add the environment variable `PARTNER_CONFIRM_TOKEN` with a long, random secret.
   Do not commit secrets to GitHub.
5. Deploy. Render provides an HTTPS URL and manages the TLS certificate for its
   `onrender.com` domain. Open the generated `https://...onrender.com` address.
6. If using a custom domain, add it in Render and follow its DNS/TLS setup instructions.

### Security notes
- HTTPS encrypts traffic in transit; it does not by itself secure the application.
- This prototype still uses a browser-generated demo wallet ID rather than real
  authentication. Do not use it to store real user accounts, valuable points, or
  real store discounts until authentication/authorization, rate limiting, secure
  session handling, production database configuration/backups, and store-side coupon
  validation are implemented.
- Use a managed PostgreSQL database for a deployed service. The local SQLite database
  file may not persist across redeploys or work reliably with multiple service instances.
- Never put secret tokens or database credentials in frontend JavaScript or commit
  them to the repository.
- For local development, `http://127.0.0.1:8000` is expected. Do not enter real or
  sensitive personal information into the demo.
