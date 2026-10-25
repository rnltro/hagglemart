# HaggleMart — the store that haggles back

> **A storefront where an AI negotiator bargains with every buyer — 24/7, never below your secret floor price — then closes the deal straight into a PayPal order. Receipt, refunds, and stock update themselves.**

📄 **[One-page pitch (PDF)](HaggleMart-Pitch.pdf)**

## The problem

**Fixed prices lose sales. Manual haggling loses time.**

- A sticker price on negotiable goods is a gamble: too high and interested buyers walk away silently, too low and you give away margin you didn't need to.
- Sellers who negotiate by hand (DMs, WhatsApp, marketplace offers) do it one buyer at a time — inconsistently — and buyer A's better deal always leaks to buyer B.
- Worst of all: a price agreed *in chat* is uncoupled from payment. The deal dies between "yes" and "paid."

## Who it's for

Sellers of **negotiable goods** — vintage & collectibles, antiques, art, furniture, resale electronics — especially those moving inventory through Facebook Marketplace, Instagram, and WhatsApp chats who want a real storefront without marketplace fees. Not for commodity retail where haggling isn't expected.

## What it solves

| Problem | HaggleMart's answer |
|---|---|
| Price wall kills conversion | Bot bargains in 3–5 rounds with a randomized concession path — hesitant buyers get a deal instead of a bounce |
| Inconsistent manual pricing | Secret per-product `floor_price`; the bot never crosses it and every buyer gets the same rules |
| Agreement ≠ payment | The negotiated price flows **directly into a PayPal order** — one click to pay, nothing lost between deal and paid |
| No trust artifacts | Itemized thermal-style receipt + printable PDF, real PayPal capture, full/partial refunds |
| Ops overhead | Stock auto-decrements on capture, sold-out items refused, PIN-gated merchant panel |
| Checkout friction | No account, no email required — PayPal auto-emails the receipt when the buyer logs in |

**The bargaining itself is the product.** Everyone else either automates the store and loses the haggle, or keeps the haggle in chat and loses the sale. HaggleMart is the only layer that does both — negotiation and payment — in one flow.

What it does
HaggleMart is a complete small storefront with bargaining built into the product, not bolted on.

Buyer flow. Pick an item in 🛍️ Shop, then chat with Marco — a market-stall negotiator who greets you, answers product FAQs, evaluates offers, counters with personality, and shakes hands in 3–5 rounds. The moment a deal is struck, the agreed price becomes a PayPal order: approve it on PayPal, get redirected back, and land on 🧾 Receipts with a styled HTML + PDF receipt. No account, no forms.

Merchant flow. Everything shop-owner-ish is invisible to buyers. Set a MERCHANT_PIN and the whole sidebar stays auto-hidden until you unlock the 🔐 Merchant area with that PIN; lock or reset and it vanishes again. Behind the PIN you get the catalog editor (name, asking price, secret floor, stock, description), recent orders, full/partial refunds, restock, and system diagnostics.

House rules the bot enforces. The discounted price exists only for exactly two units — a pair deal priced at max(floor, 5/6 × asking) per unit (e.g. the $180 camera: 2 for $300, 16.7% off). Offers for 3+ units below asking get a polite no: "the special price is strictly for two." Single-unit haggling follows a randomized concession ladder that always lands above your secret floor.

How the negotiation engine works (the trust part)
haggle_bot.py is a strict two-layer design:

Layer 1 — deterministic negotiation engine. Regex parsers for offers, quantities, and FAQ intents; a hidden concession ladder θ = [0.65, 0.40, 0.18, 0.05, 0.0] with round counters a_r = floor + (asking − floor) · θ_r; accept-threshold checks; pair-deal anchoring; stock and round limits. Every money decision is made here — never by a model.
Layer 2 — LLM voice (optional). An OpenAI-compatible chat completion (OpenAI, Groq, Z.ai GLM, local Ollama — anything) receives the engine's decision plus an explicit allow-list of prices it may quote, and only phrases the reply. A prompt-injection defuser redacts attacks ("ignore previous instructions", persona hijacks, Unicode homoglyphs) before the model sees them; a post-generation sanitizer deletes any reply that echoes an injection or contains a non-authorized amount — a floor value in any Unicode form wipes the whole output. If the LLM fails for any reason, a deterministic voice takes over and commerce continues.
The seller's floor_price is mathematically unreachable: 22 dedicated pytest tests — including a 200-iteration offer fuzz and a 60-iteration prompt-injection fuzz — prove no message sequence can settle below floor or make it leak.

How payment works (the reliability part)
PayPal Orders v2 REST API: create order at the negotiated amount → buyer approves via PayPal's hosted approval link → server-side capture on return. Four redundant capture paths — browser redirect return, CHECKOUT.ORDER.APPROVED webhook, background poller, and a CREATED-order reconciliation pass — mean a deal is captured exactly once even if the buyer closes the tab or the Wi-Fi dies mid-redirect. Idempotency is enforced by a single settlement ledger keyed by PayPal order id. Fully sandbox-driven for the demo; flip PAYPAL_MODE to live with live keys when you're ready for real money.

🚀 Quick start (local)
Requirements: Python 3.10+.

git clone <your-repo-url> && cd hagglemartpython -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activatepip install -r requirements.txt# secretsmkdir -p .streamlitcp .streamlit/secrets.toml.example .streamlit/secrets.toml#   → paste your PayPal SANDBOX credentials (developer.paypal.com →#     Apps & Credentials → Sandbox) and pick a MERCHANT_PINstreamlit run app.py
Then: pick an item → type "I offer 150" in the chat → counter/accept → approve on PayPal (sandbox buyer account) → receipt. Run the test suite with python -m pytest tests/ -q.

☁️ Deploy to Streamlit Community Cloud (~10 min, $0)
Push the whole project to a public GitHub repo — every module the app imports (app.py, haggle_bot.py, cart.py, store.py, paypal_client.py, receipt.py, mailer.py, poller.py, webhook_server.py, fulfillment.py), plus products.json, requirements.txt, .streamlit/secrets.toml.example (never secrets.toml itself), tests/, and an open-source LICENSE at repo top level. A .gitignore with .streamlit/secrets.toml, *.db, __pycache__/ covers the rest.
Go to share.streamlit.io → sign in with GitHub → Create app → pick the repo/branch → main file path app.py → Deploy. First build takes ~2–3 minutes; you get a URL like https://yourname-hagglemart.streamlit.app.
Add secrets in the cloud: app page → ⚙️ Settings → Secrets → paste the TOML below (same keys as local, never committed):
PAYPAL_CLIENT_ID = "AYxxxxxxxxxxxxxxxxxxxx"PAYPAL_SECRET    = "EHxxxxxxxxxxxxxxxxxxxx"PAYPAL_MODE      = "sandbox"MERCHANT_PIN    = "pin-you-choose"     # not the demo default on a public appPUBLIC_APP_URL  = "https://yourname-hagglemart.streamlit.app"# optional AI voice — omit entirely for the deterministic negotiator# LLM_API_KEY  = ""# LLM_BASE_URL = "https://api.groq.com/openai/v1"# LLM_MODEL    = "llama-3.3-70b-versatile"
Save → the app restarts with secrets loaded. Full step-by-step, host alternatives (Hugging Face Spaces, Render, VPS), and troubleshooting live in DEPLOY.md.
Cloud caveats worth knowing: the webhook thread is unreliable on shared hosts — the redirect-return + poller paths capture exactly once regardless; the filesystem is ephemeral, so orders/receipts reset on redeploy (restock via the merchant area, or pick a host with persistent storage); free-tier apps sleep when idle and need ~30–60 s to wake.

⚙️ Configuration reference
All keys live in .streamlit/secrets.toml (or environment variables — secrets win). Only the PayPal pair is required for checkout; everything else is optional.

Key	Default	Purpose
PAYPAL_CLIENT_ID / PAYPAL_SECRET	—	Required. PayPal REST credentials. Sandbox pair for demos; PAYPAL_CLIENT_SECRET is accepted as an alias for PAYPAL_SECRET.
PAYPAL_MODE	sandbox	sandbox or live.
MERCHANT_PIN	(empty)	Unlocks the 🔐 Merchant area. When set, the sidebar is auto-hidden from buyers until unlock; when empty, the (harmless) panel stays visible.
PUBLIC_APP_URL	(empty)	Public URL of this deployment — PayPal approval redirects send buyers back here. Set it to the exact cloud URL.
MERCHANT_NAME	Acme Collectibles	Display name.
AI_BOT_ENABLED	true	Master switch; false forces the deterministic voice.
LLM_API_KEY	(empty)	OpenAI-compatible API key. Empty = rules voice; the store works either way.
LLM_BASE_URL	https://api.openai.com/v1	Any OpenAI-compatible endpoint (Groq, Ollama…).
LLM_MODEL	gpt-4o-mini	Model id for the voice layer.
LLM_TEMPERATURE / LLM_TIMEOUT	0.7 / 12	Voice phrasing knobs — money is never the model's call.
BOT_NAME	Marco	Negotiator persona name.
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM	(empty)	Optional email delivery of PDF receipts to addresses buyers mention in chat.
DB_PATH	(store default)	SQLite file path for orders/receipts.
ENABLE_POLLER / POLLER_INTERVAL	true / 60	Background capture poller (seconds).
ENABLE_WEBHOOK / PAYPAL_WEBHOOK_ID / WEBHOOK_PORT / PAYPAL_INSECURE_WEBHOOKS	true / — / 8600 / false	Webhook receiver; needs a webhook id (or explicit insecure mode) to start.
🧪 Testing
python -m pytest tests/ -q
The suite covers the negotiation contract (state machine, counters, accept rules), the pair-deal house rule, sold-out and qty-cap behavior, the LLM-failure fallback, rogue-LLM sanitizer wipes, and the two fuzzes: 200 randomized offer sequences (floor never crossed) and 60 prompt-injection payloads (floor never leaks). Run it from the repo root on Python 3.10+.

📁 Project structure
├── app.py               # Streamlit UI: 🛍️ Shop · 💬 Haggle & Checkout · 🧾 Receipts + runtime bootstrap├── haggle_bot.py        # negotiation engine (money decisions) + optional LLM voice + sanitizer├── cart.py              # deal cart: lines, totals, chat-driven edits, accept handling├── store.py             # SQLite orders/receipts + settlement ledger (idempotent capture)├── paypal_client.py     # PayPal Orders v2 REST: create / capture / get / refund├── receipt.py           # styled HTML + fpdf2 PDF receipts├── mailer.py            # optional SMTP receipt email├── poller.py            # background capture poller (redundant path #3)├── webhook_server.py    # threaded webhook receiver (redundant path #2)├── fulfillment.py       # stock decrement + receipt email on capture (safe no-ops)├── products.json        # catalog: name, description, asking price, secret floor, stock, FAQs├── requirements.txt     # streamlit, requests, fpdf2├── tests/               # 22 tests incl. offer-fuzz (200×) and injection-fuzz (60×)└── .streamlit/secrets.toml.example   # copy to secrets.toml and fill in — never commit the real one