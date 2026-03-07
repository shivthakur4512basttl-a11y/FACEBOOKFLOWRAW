
import streamlit as st
import os
import requests
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---------------------------------------------------------------------
INSTA_APP_ID = os.getenv("INSTA_APP_ID")
INSTA_APP_SECRET = os.getenv("INSTA_APP_SECRET")
EMBED_URL = os.getenv("INSTA_EMBED_URL")
API_VERSION = "v24.0"
INSTA_REDIRECT_URI = "https://facebookflowbasttl.streamlit.app/redirect"

# --- HELPERS ---------------------------------------------------------------------
def parse_ts(ts: str):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)

def metric_value_from_insights(media_item: dict, metric_name: str) -> int:
    for m in media_item.get("insights", {}).get("data", []):
        if m.get("name") == metric_name:
            vals = m.get("values", [])
            if vals and isinstance(vals, list):
                return int(vals[0].get("value", 0) or 0)
            return int(m.get("value", 0) or 0)
    return 0

# --- METRICS FUNCTION (FOR 7/30/90 DAY ENGAGEMENT) --------------------------------
def fetch_instagram_metrics(access_token, ig_user_id, days, followers):
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    fields = "id,timestamp,like_count,comments_count,insights.metric(views,impressions,reach,saved,shares,total_interactions)"
    url = f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media?fields={fields}&limit=50&access_token={access_token}"

    totals = {"likes": 0, "comments": 0, "shares": 0, "saves": 0, "reach": 0, "total_interactions": 0, "post_count": 0}

    while url:
        resp = requests.get(url, timeout=10).json()
        if "data" not in resp: break

        for post in resp['data']:
            post_date = datetime.strptime(post['timestamp'], "%Y-%m-%dT%H:%M:%S%z")
            if post_date < cutoff_date:
                url = None
                break

            totals["likes"] += post.get('like_count', 0)
            totals["comments"] += post.get('comments_count', 0)
            totals["post_count"] += 1

            if 'insights' in post:
                for metric in post['insights']['data']:
                    val = metric['values'][0]['value'] if metric['values'] else 0
                    name = metric['name']
                    if name == 'shares': totals["shares"] += val
                    elif name == 'saved': totals["saves"] += val
                    elif name == 'reach': totals["reach"] += val
                    elif name == 'total_interactions': totals["total_interactions"] += val

        url = resp.get('paging', {}).get('next')

    engagement = totals["likes"] + totals["comments"] + totals["shares"] + totals["saves"]
    er = (engagement / followers * 100) if followers > 0 else 0
    return {"ER": round(er, 2), "posts": totals["post_count"], "totals": totals}

# --- MEDIA TOTALS (FOR VIEWS/SHARES/SAVED) ----------------------------------------
def fetch_media_totals(access_token, ig_user_id, days=90):
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)

    # STEP 1: get media_count
    media_res = requests.get(
        f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}?fields=media_count&access_token={access_token}"
    ).json()
    media_count = media_res.get("media_count", 100)

    BASE_URL = (
        f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media?"
        f"fields=id,caption,media_type,media_product_type,timestamp,permalink,"
        f"like_count,comments_count,insights.metric(views,shares,saved)"
        f"&limit={100 if media_count > 100 else media_count}"
        f"&access_token={access_token}"
    )

    totals = {
        "views": 0,
        "shares": 0,
        "saved": 0,
        "likes": 0,
        "comments": 0,
        "counted_media": 0,
        "skipped_old_media": 0,
    }

    next_url = BASE_URL
    while next_url:
        payload = requests.get(next_url).json()
        if "error" in payload:
            raise RuntimeError(payload["error"])

        for item in payload.get("data", []):
            ts = item.get("timestamp")
            if ts and parse_ts(ts) < cutoff_dt:
                totals["skipped_old_media"] += 1
                continue

            totals["counted_media"] += 1
            totals["likes"] += int(item.get("like_count", 0))
            totals["comments"] += int(item.get("comments_count", 0))
            totals["views"] += metric_value_from_insights(item, "views")
            totals["shares"] += metric_value_from_insights(item, "shares")
            totals["saved"] += metric_value_from_insights(item, "saved")

        next_url = payload.get("paging", {}).get("next")
        time.sleep(0.1)

    return totals

# --- STREAMLIT UI -----------------------------------------------------------------
st.set_page_config(page_title="Instagram Professional Insights", page_icon="📊", layout="wide")
st.title("📊 Instagram Professional Insights Suite")

query_params = st.query_params

if "code" not in query_params:
    st.info("👋 Please authorize your Instagram account to begin.")
    st.link_button("🚀 Login & Authorize Instagram", url=EMBED_URL, use_container_width=True)
    st.stop()

# ==================================================================================
# STEP 1: CAPTURE AND DISPLAY AUTHORIZATION CODE
# ==================================================================================
auth_code = query_params["code"].split("#_")[0]

st.success("✅ Authorization Code Received!")
with st.expander("🔑 View Authorization Code", expanded=True):
    st.code(auth_code, language=None)
    st.caption("This code is valid for 1 hour and can only be used once.")

st.divider()

with st.status("🔗 Connecting to Instagram...", expanded=True) as status:

    # ==================================================================================
    # STEP 2: EXCHANGE FOR SHORT-LIVED TOKEN
    # ==================================================================================
    st.write("🔄 Exchanging authorization code for short-lived access token...")
    token_url = "https://api.instagram.com/oauth/access_token"
    payload = {
        "client_id": INSTA_APP_ID,
        "client_secret": INSTA_APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": INSTA_REDIRECT_URI,
        "code": auth_code
    }
    token_res = requests.post(token_url, data=payload).json()
    
    # Handle the response structure - it might be nested in 'data' array or directly in response
    if "data" in token_res and isinstance(token_res["data"], list):
        token_data = token_res["data"][0]
        short_token = token_data.get("access_token")
        user_id_from_token = token_data.get("user_id")
        permissions = token_data.get("permissions", "")
    else:
        short_token = token_res.get("access_token")
        user_id_from_token = token_res.get("user_id")
        permissions = token_res.get("permissions", "")

    if not short_token:
        st.error("❌ Token exchange failed")
        st.write(token_res)
        st.stop()

    st.success("✅ Short-lived Access Token Received!")
    
    # ==================================================================================
    # STEP 3: UPGRADE TO LONG-LIVED TOKEN
    # ==================================================================================
    st.write("⬆️ Upgrading to long-lived access token (60-day validity)...")
    ll_url = "https://graph.instagram.com/access_token"
    ll_params = {
        "grant_type": "ig_exchange_token",
        "client_secret": INSTA_APP_SECRET,
        "access_token": short_token
    }
    ll_res = requests.get(ll_url, params=ll_params).json()
    access_token = ll_res.get("access_token")
    token_type = ll_res.get("token_type", "bearer")
    expires_in = ll_res.get("expires_in", 0)
    
    # Calculate expiration date
    expiration_date = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    st.success("✅ Long-lived Access Token Received!")

    # ==================================================================================
    # STEP 4: FETCH PROFILE DATA
    # ==================================================================================
    st.write("👤 Fetching profile information...")
    me_url = (
        f"https://graph.instagram.com/{API_VERSION}/me"
        f"?fields=id,user_id,username,name"
        f"&access_token={access_token}"
    )
    me_data = requests.get(me_url).json()
    app_id = me_data.get("id")
    ig_user_id = me_data.get("user_id")
    username = me_data.get("username")
    name = me_data.get("name")

    prof_url = (
        f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}"
        f"?fields=account_type,profile_picture_url,followers_count,follows_count,media_count"
        f"&access_token={access_token}"
    )
    prof_data = requests.get(prof_url).json()

    account_type = prof_data.get("account_type")
    profile_pic = prof_data.get("profile_picture_url")
    followers = prof_data.get("followers_count", 0)
    follows = prof_data.get("follows_count", 0)
    media_count = prof_data.get("media_count", 0)

    # ==================================================================================
    # STEP 5: FETCH ENGAGEMENT METRICS
    # ==================================================================================
    st.write("📊 Fetching 7/30/90 Day Engagement Metrics...")
    report_7 = fetch_instagram_metrics(access_token, ig_user_id, 7, followers)
    report_30 = fetch_instagram_metrics(access_token, ig_user_id, 30, followers)
    report_90 = fetch_instagram_metrics(access_token, ig_user_id, 90, followers)

    st.write("📸 Fetching 90-Day Media Totals...")
    media_totals = fetch_media_totals(access_token, ig_user_id, 90)

    status.update(label="✅ Data Loaded Successfully!", state="complete")

# ==================================================================================
# TOKEN DISPLAY SECTION
# ==================================================================================
st.divider()
st.markdown("### 🔐 Authentication Tokens")

token_col1, token_col2 = st.columns(2)

with token_col1:
    st.markdown("#### 🕐 Short-lived Access Token")
    st.code(short_token, language=None)
    st.caption("⏱️ Valid for a short period (typically 1 hour)")
    if user_id_from_token:
        st.caption(f"👤 User ID: `{user_id_from_token}`")
    if permissions:
        st.caption(f"🔒 Permissions: `{permissions}`")

with token_col2:
    st.markdown("#### ⏳ Long-lived Access Token")
    st.code(access_token, language=None)
    st.caption(f"⏱️ Valid for {expires_in // 86400} days (~{expires_in:,} seconds)")
    st.caption(f"📅 Expires: {expiration_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    st.caption(f"🔑 Token Type: `{token_type}`")

st.info("💡 **Token Refresh:** Your long-lived token can be refreshed for another 60 days as long as it's at least 24 hours old and still valid.")

# ==================================================================================
# TOKEN REFRESH SECTION
# ==================================================================================
st.markdown("#### 🔄 Refresh Long-lived Token")
st.caption("Use this to extend your token's validity for another 60 days. Token must be at least 24 hours old.")

if st.button("🔄 Refresh Access Token", use_container_width=True):
    with st.spinner("Refreshing token..."):
        refresh_url = "https://graph.instagram.com/refresh_access_token"
        refresh_params = {
            "grant_type": "ig_refresh_token",
            "access_token": access_token
        }
        refresh_res = requests.get(refresh_url, params=refresh_params).json()
        
        if "access_token" in refresh_res:
            new_access_token = refresh_res.get("access_token")
            new_expires_in = refresh_res.get("expires_in", 0)
            new_expiration = datetime.now(timezone.utc) + timedelta(seconds=new_expires_in)
            
            st.success("✅ Token Refreshed Successfully!")
            st.markdown("##### 🆕 New Long-lived Access Token")
            st.code(new_access_token, language=None)
            st.caption(f"⏱️ Valid for {new_expires_in // 86400} days")
            st.caption(f"📅 New Expiration: {new_expiration.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        else:
            st.error("❌ Token refresh failed")
            st.write(refresh_res)

st.divider()

# ==================================================================================
# PROFILE DISPLAY SECTION
# ==================================================================================
st.markdown("### 👤 Profile Information")

col1, col2 = st.columns([1,4])
with col1:
    st.image(profile_pic, width=120) if profile_pic else st.write("👤 No Profile Image")
with col2:
    st.subheader(f"{name} (@{username})")
    st.write(f"**Account Type:** {account_type}")
    st.write(f"**Followers:** {followers:,}")
    st.write(f"**Following:** {follows:,}")
    st.write(f"**Media Count:** {media_count:,}")
    st.write(f"**App Scoped ID:** `{app_id}`")
    st.write(f"**IG User ID:** `{ig_user_id}`")

st.divider()

# ==================================================================================
# ENGAGEMENT METRICS
# ==================================================================================
st.markdown("### 📈 Engagement Performance")
m1, m2, m3 = st.columns(3)
m1.metric("7-Day ER", f"{report_7['ER']}%", f"{report_7['posts']} posts")
m2.metric("30-Day ER", f"{report_30['ER']}%", f"{report_30['posts']} posts")
m3.metric("90-Day ER", f"{report_90['ER']}%", f"{report_90['posts']} posts")

st.divider()

# ==================================================================================
# MEDIA TOTALS
# ==================================================================================
st.markdown("### 🧾 90-Day Media Totals")
st.json(media_totals)

st.success("🎉 All Data Loaded Successfully!")
