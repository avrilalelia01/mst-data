import hmac
import hashlib
import time
import urllib.parse

PARTNER_ID  = 1246699
PARTNER_KEY = "shpk4f4465484877624446746a664765596772735a6e5243674976726d6e4f46"
REDIRECT    = "http://localhost:8888/callback"
PATH        = "/api/v2/shop/auth_partner"
BASE        = "https://partner.test-stable.shopeemobile.com"

ts   = int(time.time())
base = f"{PARTNER_ID}{PATH}{ts}"
sign = hmac.new(PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()

url = (
    f"{BASE}{PATH}"
    f"?partner_id={PARTNER_ID}"
    f"&redirect={urllib.parse.quote(REDIRECT, safe='')}"
    f"&timestamp={ts}"
    f"&sign={sign}"
)

print("\n=== Shopee Sandbox OAuth URL ===")
print(url)
print("\nInstructions:")
print("1. Open the URL above in your browser")
print("2. Log in with your Shopee sandbox seller account")
print("3. Approve the authorization")
print("4. After redirect, copy the 'shop_id' value from the URL")
print("   e.g. http://localhost:8888/callback?shop_id=12345678&code=abc123")
print("5. Update shop_id in config.yaml with that value")
print("6. Note the 'code' value too — you will need it for bootstrap_tokens.py")
