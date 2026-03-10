'''import streamlit as st
import os
import requests
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---------------------------------------------------------------------
INSTA_APP_ID = os.getenv("INSTA_APP_ID")
INSTA_APP_SECRET = os.getenv("INSTA_APP_SECRET")
EMBED_URL = os.getenv("INSTA_EMBED_URL")
API_VERSION = "v24.0"
INSTA_REDIRECT_URI = "https://facebookflowcoshotraw.streamlit.app/redirect"

# Initialize session state
if 'proceed_with_metrics' not in st.session_state:
    st.session_state.proceed_with_metrics = False

# --- HELPER FUNCTIONS ------------------------------------------------------------
def display_api_endpoint_info(
    step_number: str,
    title: str,
    method: str,
    endpoint: str,
    description: str,
    params: dict = None,
    headers: dict = None,
    body: dict = None,
    notes: list = None
):
    """Display detailed API endpoint information"""
    st.markdown(f"### 🔌 API Endpoint Details - Step {step_number}")
    
    # Endpoint info card
    with st.container():
        st.markdown(f"**{title}**")
        st.caption(description)
        
        # Method and URL
        method_color = "green" if method == "GET" else "blue"
        st.markdown(f"**Method:** :{method_color}[{method}]")
        st.code(endpoint, language=None)
        
        # Parameters
        if params:
            with st.expander("📝 Request Parameters", expanded=True):
                for key, value in params.items():
                    # Mask sensitive data
                    if key in ["client_secret", "access_token"] and value:
                        display_value = value[:20] + "..." if len(value) > 20 else value
                        st.code(f"{key}: {display_value} (truncated for security)", language=None)
                    else:
                        st.code(f"{key}: {value}", language=None)
        
        # Body (for POST requests)
        if body:
            with st.expander("📦 Request Body", expanded=True):
                st.json(body)
        
        # Headers
        if headers:
            with st.expander("📋 Request Headers", expanded=False):
                st.json(headers)
        
        # Additional notes
        if notes:
            with st.expander("📌 Important Notes", expanded=False):
                for note in notes:
                    st.markdown(f"- {note}")
    
    st.divider()

def display_json_with_download(title: str, purpose: str, data: dict, filename: str, emoji: str = "📄"):
    """Display JSON data with purpose header and download button"""
    st.markdown(f"### {emoji} {title}")
    st.caption(f"**Purpose:** {purpose}")
    
    # Download button
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    st.download_button(
        label=f"⬇️ Download {filename}.json",
        data=json_str,
        file_name=f"{filename}.json",
        mime="application/json",
        use_container_width=True
    )
    
    # Expandable JSON viewer
    with st.expander("📋 View JSON Response", expanded=False):
        st.json(data)
    
    st.divider()

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
    all_posts = []

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
            
            all_posts.append(post)

        url = resp.get('paging', {}).get('next')

    engagement = totals["likes"] + totals["comments"] + totals["shares"] + totals["saves"]
    er = (engagement / followers * 100) if followers > 0 else 0
    
    return {
        "ER": round(er, 2), 
        "posts": totals["post_count"], 
        "totals": totals,
        "raw_posts": all_posts
    }

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
    
    all_media = []

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
            
            all_media.append(item)

        next_url = payload.get("paging", {}).get("next")
        time.sleep(0.1)

    return {
        "summary": totals,
        "media_items": all_media
    }

# --- STREAMLIT UI -----------------------------------------------------------------
st.set_page_config(page_title="Instagram Professional Insights", page_icon="📊", layout="wide")
st.title("📊 Instagram Professional Insights Suite")
st.caption("Complete OAuth Flow with Full API & Insights Documentation for Developers")

query_params = st.query_params

if "code" not in query_params:
    st.info("👋 Please authorize your Instagram account to begin.")
    
    # Show OAuth flow documentation
    with st.expander("📖 How Instagram OAuth Works", expanded=True):
        st.markdown("""
        ### Instagram OAuth 2.0 Flow
        
        **Step 1: Authorization Request**
        - User clicks the authorization button
        - Redirects to Instagram's authorization page
        - User grants permissions to your app
        
        **Step 2: Authorization Code**
        - Instagram redirects back to your app with an authorization code
        - Code is valid for 1 hour and single-use only
        
        **Step 3: Token Exchange**
        - Exchange authorization code for short-lived access token
        - Upgrade to long-lived token (60 days)
        
        **Step 4: API Access**
        - Use access token to call Instagram Graph API
        - Fetch profile data, media, insights, etc.
        """)
        
        st.markdown("**Authorization URL Structure:**")
        st.code(f"""
https://www.instagram.com/oauth/authorize
  ?client_id=YOUR_APP_ID
  &redirect_uri=YOUR_REDIRECT_URI
  &response_type=code
  &scope=instagram_business_basic,instagram_business_manage_messages,instagram_business_manage_comments,instagram_business_content_publish
        """, language=None)
    
    st.link_button("🚀 Login & Authorize Instagram", url=EMBED_URL, use_container_width=True)
    st.stop()

# ==================================================================================
# STEP 1: CAPTURE AND DISPLAY AUTHORIZATION CODE
# ==================================================================================
auth_code = query_params["code"]
if isinstance(auth_code, list):
    auth_code = auth_code[0]
auth_code = auth_code.split("#_")[0]

# Create authorization code response object
auth_code_response = {
    "authorization_code": auth_code,
    "received_at": datetime.now(timezone.utc).isoformat(),
    "source": "Instagram OAuth Redirect",
    "redirect_uri": INSTA_REDIRECT_URI,
    "valid_for": "1 hour",
    "single_use": True
}

st.success("✅ Authorization Code Received!")

# Display OAuth redirect information
display_api_endpoint_info(
    step_number="1",
    title="OAuth Authorization Redirect",
    method="GET",
    endpoint=f"{INSTA_REDIRECT_URI}?code={auth_code[:20]}...#_",
    description="Instagram redirects the user back to your app with an authorization code in the query parameters.",
    params={
        "code": f"{auth_code[:30]}... (authorization code)",
        "state": "(optional) server-specific state for CSRF protection"
    }
)

display_json_with_download(
    title="1️⃣ Authorization Code",
    purpose="The authorization code received from Instagram OAuth redirect. This code will be exchanged for an access token.",
    data=auth_code_response,
    filename="authorizationCode",
    emoji="🔑"
)

# ==================================================================================
# PAUSE HERE - WAIT FOR USER TO CLICK BUTTON
# ==================================================================================
st.markdown("---")
st.markdown("## ⏸️ Ready to Proceed?")

st.info("""
**👇 Click the button below to start fetching all metrics**

This will:
- Exchange authorization code for access tokens
- Fetch your Instagram profile data
- Calculate 7-day, 30-day, and 90-day engagement metrics
- Retrieve all media insights and totals

⚠️ **Note:** This process will make multiple API calls to Instagram and may take 30-60 seconds to complete.
""")

# Big button to proceed
if st.button("🚀 Go Ahead - Calculate All Metrics", type="primary", use_container_width=True):
    st.session_state.proceed_with_metrics = True
    st.rerun()

# Stop here if button hasn't been clicked
if not st.session_state.proceed_with_metrics:
    st.caption("💡 Take your time to review the authorization code above before proceeding.")
    st.stop()

# ==================================================================================
# API CALLS SECTION (ONLY RUNS AFTER BUTTON CLICK)
# ==================================================================================
st.markdown("---")
st.markdown("## 🔄 API Request Flow")
st.success("✅ Processing started! Fetching all metrics...")

with st.status("🔗 Processing Instagram Authentication...", expanded=True) as status:

    # ==================================================================================
    # STEP 2: EXCHANGE FOR SHORT-LIVED TOKEN
    # ==================================================================================
    st.write("🔄 Step 2: Exchanging authorization code for short-lived access token...")
    
    token_url = "https://api.instagram.com/oauth/access_token"
    token_payload = {
        "client_id": INSTA_APP_ID,
        "client_secret": INSTA_APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": INSTA_REDIRECT_URI,
        "code": auth_code
    }
    
    token_res = requests.post(token_url, data=token_payload).json()
    
    # Handle the response structure
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
        st.json(token_res)
        st.stop()

    st.success("✅ Short-lived Access Token Received!")
    
    # ==================================================================================
    # STEP 3: UPGRADE TO LONG-LIVED TOKEN
    # ==================================================================================
    st.write("⬆️ Step 3: Upgrading to long-lived access token (60-day validity)...")
    
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
    expiration_date = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    st.success("✅ Long-lived Access Token Received!")

    # ==================================================================================
    # STEP 4: FETCH BASE PROFILE DATA
    # ==================================================================================
    st.write("👤 Step 4: Fetching base profile information...")
    
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

    st.success("✅ Base Profile Data Received!")

    # ==================================================================================
    # STEP 5: FETCH PROFESSIONAL ACCOUNT DATA
    # ==================================================================================
    st.write("📊 Step 5: Fetching professional account details...")
    
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

    st.success("✅ Professional Account Data Received!")

    # ==================================================================================
    # STEP 6: FETCH 7-DAY ENGAGEMENT METRICS
    # ==================================================================================
    st.write("📈 Step 6: Fetching 7-day engagement metrics...")
    report_7 = fetch_instagram_metrics(access_token, ig_user_id, 7, followers)
    st.success("✅ 7-Day Metrics Retrieved!")

    # ==================================================================================
    # STEP 7: FETCH 30-DAY ENGAGEMENT METRICS
    # ==================================================================================
    st.write("📈 Step 7: Fetching 30-day engagement metrics...")
    report_30 = fetch_instagram_metrics(access_token, ig_user_id, 30, followers)
    st.success("✅ 30-Day Metrics Retrieved!")

    # ==================================================================================
    # STEP 8: FETCH 90-DAY ENGAGEMENT METRICS
    # ==================================================================================
    st.write("📈 Step 8: Fetching 90-day engagement metrics...")
    report_90 = fetch_instagram_metrics(access_token, ig_user_id, 90, followers)
    st.success("✅ 90-Day Metrics Retrieved!")

    # ==================================================================================
    # STEP 9: FETCH 90-DAY MEDIA TOTALS
    # ==================================================================================
    st.write("📸 Step 9: Fetching 90-day media totals...")
    media_totals = fetch_media_totals(access_token, ig_user_id, 90)
    st.success("✅ Media Totals Retrieved!")

    status.update(label="✅ All Data Loaded Successfully!", state="complete")

# ==================================================================================
# DETAILED API DOCUMENTATION SECTION
# ==================================================================================
st.markdown("---")
st.markdown("## 📚 Complete API Documentation")
st.caption("Full details of every API call made in this application")

# STEP 2: Short-lived token exchange
display_api_endpoint_info(
    step_number="2",
    title="Exchange Authorization Code for Short-lived Token",
    method="POST",
    endpoint="https://api.instagram.com/oauth/access_token",
    description="Exchange the authorization code for a short-lived access token. This token is valid for approximately 1 hour.",
    body=token_payload,
    notes=[
        "This endpoint requires a POST request with form data",
        "The authorization code can only be used once",
        "Response includes access_token, user_id, and granted permissions"
    ]
)

display_json_with_download(
    title="2️⃣ Short-lived Access Token Response",
    purpose="Response from exchanging authorization code for a short-lived access token (valid ~1 hour).",
    data=token_res,
    filename="shortLivedToken",
    emoji="🕐"
)

# STEP 3: Long-lived token upgrade
display_api_endpoint_info(
    step_number="3",
    title="Upgrade to Long-lived Token",
    method="GET",
    endpoint=f"https://graph.instagram.com/access_token",
    description="Exchange a valid short-lived token for a long-lived token that is valid for 60 days.",
    params={
        "grant_type": "ig_exchange_token",
        "client_secret": INSTA_APP_SECRET,
        "access_token": short_token
    },
    notes=[
        "Short-lived token must be valid (not expired)",
        "Long-lived tokens expire in 60 days",
        "Tokens can be refreshed if at least 24 hours old"
    ]
)

display_json_with_download(
    title="3️⃣ Long-lived Access Token Response",
    purpose="Response from upgrading short-lived token to long-lived token (valid 60 days).",
    data=ll_res,
    filename="longLivedToken",
    emoji="⏳"
)

# STEP 4: Base profile
display_api_endpoint_info(
    step_number="4",
    title="Get Base Profile Information",
    method="GET",
    endpoint=f"https://graph.instagram.com/{API_VERSION}/me",
    description="Retrieve basic profile information including app-scoped ID, Instagram user ID, username, and name.",
    params={
        "fields": "id,user_id,username,name",
        "access_token": access_token
    },
    notes=[
        "The 'id' field is your app-scoped user ID",
        "The 'user_id' field is the Instagram Business Account ID",
        "Use 'user_id' for all subsequent API calls"
    ]
)

display_json_with_download(
    title="4️⃣ Base Profile Response",
    purpose="Basic profile information from /me endpoint including app-scoped ID and Instagram user ID.",
    data=me_data,
    filename="baseProfile",
    emoji="👤"
)

# STEP 5: Professional account
display_api_endpoint_info(
    step_number="5",
    title="Get Professional Account Details",
    method="GET",
    endpoint=f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}",
    description="Retrieve professional account information including account type, profile picture, followers count, following count, and media count.",
    params={
        "fields": "account_type,profile_picture_url,followers_count,follows_count,media_count",
        "access_token": access_token
    },
    notes=[
        "Only works for Business or Creator accounts",
        "account_type can be 'BUSINESS' or 'CREATOR'",
        "followers_count and media_count are public metrics"
    ]
)

display_json_with_download(
    title="5️⃣ Professional Account Response",
    purpose="Professional account details including followers, account type, and media count.",
    data=prof_data,
    filename="professionalAccount",
    emoji="💼"
)

# ==================================================================================
# INSIGHTS API DOCUMENTATION
# ==================================================================================
st.markdown("---")
st.markdown("## 📊 Instagram Insights API Documentation")
st.caption("Detailed documentation for fetching engagement metrics and insights")

# Create insights documentation
insights_info = {
    "endpoint_base": f"https://graph.instagram.com/{API_VERSION}/{{ig_user_id}}/media",
    "available_metrics": {
        "views": "Total number of times the media has been viewed (Reels only)",
        "impressions": "Total number of times the media has been seen",
        "reach": "Total number of unique accounts that have seen the media",
        "saved": "Total number of unique accounts that have saved the media",
        "shares": "Total number of times the media has been shared",
        "total_interactions": "Sum of likes, comments, saves, and shares",
        "like_count": "Number of likes (available without insights)",
        "comments_count": "Number of comments (available without insights)"
    },
    "media_types": {
        "IMAGE": "Single photo post",
        "VIDEO": "Video post",
        "CAROUSEL_ALBUM": "Album with multiple photos/videos",
        "REELS": "Reel (short-form video)"
    },
    "metric_availability": {
        "All Media Types": ["impressions", "reach", "saved", "shares", "total_interactions"],
        "Reels Only": ["views"],
        "Always Available": ["like_count", "comments_count", "timestamp", "permalink"]
    }
}

with st.expander("📖 Insights API Overview", expanded=True):
    st.markdown("""
    ### What are Instagram Insights?
    
    Instagram Insights provide detailed analytics about your media posts including:
    - **Engagement metrics** (likes, comments, shares, saves)
    - **Reach metrics** (impressions, reach, views)
    - **Interaction metrics** (total_interactions)
    
    ### How to Request Insights
    
    Insights are requested as part of the media fields using the special syntax:
    ```
    insights.metric(metric_name1,metric_name2,...)
    ```
    
    ### Important Notes
    - Insights require the `instagram_business_basic` permission
    - Some metrics are only available for specific media types
    - Insights data is only available for media owned by the authenticated account
    - Historical data may have limited availability depending on when the post was created
    """)
    
    st.markdown("### Available Metrics")
    for metric, description in insights_info["available_metrics"].items():
        st.markdown(f"- **`{metric}`**: {description}")
    
    st.markdown("### Metric Availability by Media Type")
    for media_type, metrics in insights_info["metric_availability"].items():
        st.markdown(f"**{media_type}**: {', '.join(metrics)}")

# STEP 6: 7-day metrics with insights
media_fields = "id,timestamp,like_count,comments_count,insights.metric(views,impressions,reach,saved,shares,total_interactions)"
display_api_endpoint_info(
    step_number="6",
    title="Get 7-Day Engagement Metrics with Insights",
    method="GET",
    endpoint=f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media",
    description="Retrieve all media posts from the last 7 days with engagement metrics including likes, comments, shares, saves, reach, and views. Uses the Insights API to fetch detailed analytics.",
    params={
        "fields": media_fields,
        "limit": "50",
        "access_token": access_token
    },
    notes=[
        "The insights.metric() syntax requests specific insight metrics",
        "Available metrics: views, impressions, reach, saved, shares, total_interactions",
        "Response includes pagination for handling large datasets",
        "Each media item will have an 'insights' object with the requested metrics",
        "Filter by timestamp to get posts from the last 7 days"
    ]
)

# Show example insights response structure
with st.expander("📋 Example Insights Response Structure", expanded=False):
    st.markdown("**Single Media Item with Insights:**")
    st.json({
        "id": "18123456789012345",
        "timestamp": "2024-03-01T10:30:00+0000",
        "like_count": 142,
        "comments_count": 23,
        "insights": {
            "data": [
                {"name": "views", "period": "lifetime", "values": [{"value": 1523}]},
                {"name": "impressions", "period": "lifetime", "values": [{"value": 2341}]},
                {"name": "reach", "period": "lifetime", "values": [{"value": 1876}]},
                {"name": "saved", "period": "lifetime", "values": [{"value": 45}]},
                {"name": "shares", "period": "lifetime", "values": [{"value": 67}]},
                {"name": "total_interactions", "period": "lifetime", "values": [{"value": 277}]}
            ]
        }
    })

display_json_with_download(
    title="6️⃣ 7-Day Engagement Metrics",
    purpose="Engagement metrics for the last 7 days including ER, likes, comments, shares, and saves with full insights data.",
    data=report_7,
    filename="sevenDayMetrics",
    emoji="📊"
)

# STEP 7: 30-day metrics
display_api_endpoint_info(
    step_number="7",
    title="Get 30-Day Engagement Metrics with Insights",
    method="GET",
    endpoint=f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media",
    description="Retrieve all media posts from the last 30 days with engagement metrics including likes, comments, shares, saves, reach, and views.",
    params={
        "fields": media_fields,
        "limit": "50",
        "access_token": access_token
    },
    notes=[
        "Same endpoint as 7-day metrics but filters for 30-day window",
        "May require multiple paginated requests for active accounts",
        "Calculates Engagement Rate (ER) based on total engagement vs followers"
    ]
)

display_json_with_download(
    title="7️⃣ 30-Day Engagement Metrics",
    purpose="Engagement metrics for the last 30 days including ER, likes, comments, shares, and saves with full insights data.",
    data=report_30,
    filename="thirtyDayMetrics",
    emoji="📊"
)

# STEP 8: 90-day metrics
display_api_endpoint_info(
    step_number="8",
    title="Get 90-Day Engagement Metrics with Insights",
    method="GET",
    endpoint=f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media",
    description="Retrieve all media posts from the last 90 days with engagement metrics including likes, comments, shares, saves, reach, and views.",
    params={
        "fields": media_fields,
        "limit": "50",
        "access_token": access_token
    },
    notes=[
        "Provides longer-term trend analysis over 90 days",
        "Useful for identifying content performance patterns",
        "May result in large datasets requiring pagination handling"
    ]
)

display_json_with_download(
    title="8️⃣ 90-Day Engagement Metrics",
    purpose="Engagement metrics for the last 90 days including ER, likes, comments, shares, and saves with full insights data.",
    data=report_90,
    filename="ninetyDayMetrics",
    emoji="📊"
)

# STEP 9: Media totals
media_total_fields = "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count,insights.metric(views,shares,saved)"
display_api_endpoint_info(
    step_number="9",
    title="Get 90-Day Media Totals with Insights",
    method="GET",
    endpoint=f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media",
    description="Retrieve complete media totals for the last 90 days including views, shares, saved counts, likes, and comments with full media details and permalinks.",
    params={
        "fields": media_total_fields,
        "limit": "100",
        "access_token": access_token
    },
    notes=[
        "Includes complete media metadata (caption, type, permalink)",
        "Focuses on views, shares, and saved metrics",
        "Provides full post URLs via permalink field",
        "Useful for detailed content analysis and reporting"
    ]
)

display_json_with_download(
    title="9️⃣ 90-Day Media Totals",
    purpose="Complete media totals for the last 90 days including views, shares, and saved counts with full media metadata.",
    data=media_totals,
    filename="ninetyDayMediaTotals",
    emoji="📸"
)

# ==================================================================================
# INSIGHTS CALCULATION EXAMPLES
# ==================================================================================
st.markdown("---")
st.markdown("## 🧮 Insights Calculations & Formulas")

with st.expander("📐 How Engagement Rate (ER) is Calculated", expanded=True):
    st.markdown("""
    ### Engagement Rate Formula
    
    ```
    ER = (Total Engagement / Followers) × 100
    ```
    
    **Total Engagement includes:**
    - Likes
    - Comments  
    - Shares
    - Saves
    
    ### Example Calculation
    
    ```python
    # Given data
    likes = 150
    comments = 25
    shares = 30
    saves = 20
    followers = 5000
    
    # Calculate total engagement
    total_engagement = likes + comments + shares + saves
    # total_engagement = 225
    
    # Calculate ER
    engagement_rate = (total_engagement / followers) * 100
    # engagement_rate = (225 / 5000) * 100 = 4.5%
    ```
    
    ### Industry Benchmarks
    - **Excellent**: 3-6%
    - **Good**: 1-3%
    - **Average**: 0.5-1%
    - **Low**: <0.5%
    """)

with st.expander("📊 How to Extract Insights from API Response", expanded=True):
    st.markdown("""
    ### Parsing Insights Data
    
    The insights are nested in the response under the `insights` key:
    
    ```python
    def get_insight_value(media_item, metric_name):
        # Navigate to insights data
        insights = media_item.get("insights", {}).get("data", [])
        
        # Find the specific metric
        for metric in insights:
            if metric.get("name") == metric_name:
                values = metric.get("values", [])
                if values:
                    return values[0].get("value", 0)
        
        return 0
    
    # Usage
    views = get_insight_value(post, "views")
    shares = get_insight_value(post, "shares")
    saved = get_insight_value(post, "saved")
    ```
    
    ### Example Response Structure
    
    ```json
    {
      "insights": {
        "data": [
          {
            "name": "impressions",
            "period": "lifetime",
            "values": [{"value": 1523}],
            "title": "Impressions",
            "description": "Total number of times the media object has been seen",
            "id": "media_id/insights/impressions/lifetime"
          }
        ]
      }
    }
    ```
    """)

# ==================================================================================
# SUMMARY DASHBOARD
# ==================================================================================
st.markdown("---")
st.markdown("## 📊 Dashboard Summary")

# Profile header
col1, col2 = st.columns([1, 4])
with col1:
    if profile_pic:
        st.image(profile_pic, width=120)
    else:
        st.write("👤 No Profile Image")
with col2:
    st.subheader(f"{name} (@{username})")
    st.write(f"**Account Type:** {account_type}")
    st.write(f"**Followers:** {followers:,}")
    st.write(f"**Following:** {follows:,}")
    st.write(f"**Media Count:** {media_count:,}")
    st.write(f"**App Scoped ID:** `{app_id}`")
    st.write(f"**IG User ID:** `{ig_user_id}`")

st.divider()

# Token information
st.markdown("### 🔐 Active Tokens")
token_col1, token_col2 = st.columns(2)

with token_col1:
    st.markdown("#### 🕐 Short-lived Token")
    st.code(short_token, language=None)
    st.caption("⏱️ Valid for ~1 hour")
    if user_id_from_token:
        st.caption(f"👤 User ID: `{user_id_from_token}`")

with token_col2:
    st.markdown("#### ⏳ Long-lived Token")
    st.code(access_token, language=None)
    st.caption(f"⏱️ Valid for {expires_in // 86400} days")
    st.caption(f"📅 Expires: {expiration_date.strftime('%Y-%m-%d %H:%M:%S UTC')}")

st.divider()

# Engagement metrics
st.markdown("### 📈 Engagement Performance")
m1, m2, m3 = st.columns(3)
m1.metric("7-Day ER", f"{report_7['ER']}%", f"{report_7['posts']} posts")
m2.metric("30-Day ER", f"{report_30['ER']}%", f"{report_30['posts']} posts")
m3.metric("90-Day ER", f"{report_90['ER']}%", f"{report_90['posts']} posts")

st.divider()

# Media totals summary
st.markdown("### 📸 90-Day Media Summary")
summary_data = media_totals.get("summary", {})
metric_cols = st.columns(5)
metric_cols[0].metric("Views", f"{summary_data.get('views', 0):,}")
metric_cols[1].metric("Shares", f"{summary_data.get('shares', 0):,}")
metric_cols[2].metric("Saves", f"{summary_data.get('saved', 0):,}")
metric_cols[3].metric("Likes", f"{summary_data.get('likes', 0):,}")
metric_cols[4].metric("Comments", f"{summary_data.get('comments', 0):,}")

# ==================================================================================
# DEVELOPER RESOURCES
# ==================================================================================
st.markdown("---")
st.markdown("## 🎓 Developer Resources")

with st.expander("📖 Instagram Graph API Documentation Links"):
    st.markdown("""
    ### Official Documentation
    
    - [Instagram Platform Overview](https://developers.facebook.com/docs/instagram-platform)
    - [Business Login for Instagram](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login)
    - [Instagram API Reference](https://developers.facebook.com/docs/instagram-platform/reference)
    - [Insights API](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/insights)
    - [Media Insights](https://developers.facebook.com/docs/instagram-platform/reference/ig-media/insights)
    
    ### Key Concepts
    
    **Access Tokens:**
    - Short-lived tokens expire in ~1 hour
    - Long-lived tokens expire in 60 days
    - Tokens can be refreshed if they're at least 24 hours old
    
    **Insights Metrics:**
    - Available metrics vary by media type (IMAGE, VIDEO, CAROUSEL_ALBUM, REELS)
    - Some metrics are only available for certain account types
    - Insights data has retention limits (typically 2 years)
    
    **Rate Limits:**
    - Instagram Graph API has rate limits per app and per user
    - Implement exponential backoff for failed requests
    - Use pagination for large data sets
    
    **Required Permissions:**
    - `instagram_business_basic` - Read basic profile info and insights
    - `instagram_business_manage_messages` - Manage messages
    - `instagram_business_manage_comments` - Manage comments
    - `instagram_business_content_publish` - Publish content
    """)

with st.expander("💻 Code Examples for Developers"):
    st.markdown("### Python Example: Fetch Media with Insights")
    st.code("""
import requests

# Fetch media with insights
access_token = "YOUR_ACCESS_TOKEN"
ig_user_id = "YOUR_IG_USER_ID"

response = requests.get(
    f"https://graph.instagram.com/v24.0/{ig_user_id}/media",
    params={
        "fields": "id,timestamp,like_count,comments_count,insights.metric(impressions,reach,saved,shares)",
        "limit": 25,
        "access_token": access_token
    }
)

media_data = response.json()

# Process each post
for post in media_data.get("data", []):
    post_id = post["id"]
    likes = post.get("like_count", 0)
    comments = post.get("comments_count", 0)
    
    # Extract insights
    insights = post.get("insights", {}).get("data", [])
    for metric in insights:
        metric_name = metric["name"]
        metric_value = metric["values"][0]["value"]
        print(f"{metric_name}: {metric_value}")
    """, language="python")
    
    st.markdown("### Python Example: Calculate Engagement Rate")
    st.code("""
import requests
from datetime import datetime, timedelta, timezone

def calculate_engagement_rate(access_token, ig_user_id, followers_count, days=30):
    # Calculate cutoff date
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    # Fetch media with insights
    url = f"https://graph.instagram.com/v24.0/{ig_user_id}/media"
    params = {
        "fields": "id,timestamp,like_count,comments_count,insights.metric(saved,shares)",
        "limit": 50,
        "access_token": access_token
    }
    
    total_engagement = 0
    post_count = 0
    
    while url:
        response = requests.get(url, params=params).json()
        
        for post in response.get("data", []):
            # Check if post is within date range
            post_date = datetime.fromisoformat(post["timestamp"].replace("Z", "+00:00"))
            if post_date < cutoff_date:
                break
            
            # Add likes and comments
            engagement = post.get("like_count", 0) + post.get("comments_count", 0)
            
            # Add insights (shares and saves)
            for metric in post.get("insights", {}).get("data", []):
                if metric["name"] in ["shares", "saved"]:
                    engagement += metric["values"][0]["value"]
            
            total_engagement += engagement
            post_count += 1
        
        # Get next page
        url = response.get("paging", {}).get("next")
        params = {}  # Next URL already includes params
    
    # Calculate ER
    er = (total_engagement / followers_count) * 100 if followers_count > 0 else 0
    
    return {
        "engagement_rate": round(er, 2),
        "total_engagement": total_engagement,
        "post_count": post_count,
        "period_days": days
    }

# Usage
result = calculate_engagement_rate(access_token, ig_user_id, 5000, days=30)
print(f"30-Day ER: {result['engagement_rate']}%")
print(f"Total Engagement: {result['total_engagement']}")
print(f"Posts Analyzed: {result['post_count']}")
    """, language="python")

st.success("🎉 All Data Loaded and Documented Successfully!")
'''

import streamlit as st
import os
import requests
import json
import time
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---------------------------------------------------------------------
INSTA_APP_ID = os.getenv("INSTA_APP_ID")
INSTA_APP_SECRET = os.getenv("INSTA_APP_SECRET")
EMBED_URL = os.getenv("INSTA_EMBED_URL")
API_VERSION = "v24.0"
INSTA_REDIRECT_URI = "https://facebookflowbasttl.streamlit.app/redirect"

# Required permissions
REQUIRED_PERMISSIONS = {
    "instagram_business_basic": "CRITICAL - Required for insights, profile data, and media",
    "instagram_business_manage_messages": "Required for messaging features",
    "instagram_business_manage_comments": "Required for comment management",
    "instagram_business_content_publish": "Required for publishing content"
}

if 'proceed_with_metrics' not in st.session_state:
    st.session_state.proceed_with_metrics = False

# --- LOGGING & ERROR FUNCTIONS --------------------------------------------------
def log_api_call(endpoint: str, method: str, response_data: dict, status: str = "SUCCESS"):
    """Log API call to terminal"""
    timestamp = datetime.now(timezone.utc).isoformat()
    print("\n" + "="*80)
    print(f"📡 API CALL - {timestamp}")
    print(f"Status: {status}")
    print(f"Method: {method}")
    print(f"Endpoint: {endpoint}")
    print("-"*80)
    print("Response:")
    print(json.dumps(response_data, indent=2)[:2000])  # Limit output
    print("="*80 + "\n")
    sys.stdout.flush()

def log_error(error_context: str, error_data: dict):
    """Log error to terminal"""
    timestamp = datetime.now(timezone.utc).isoformat()
    print("\n" + "="*80)
    print(f"❌ ERROR - {timestamp}")
    print(f"Context: {error_context}")
    print("-"*80)
    print(json.dumps(error_data, indent=2))
    print("="*80 + "\n")
    sys.stdout.flush()
    return error_data

def display_api_error(error_context: str, error_response: dict, step_name: str = ""):
    """Display API error on page"""
    st.error(f"❌ **API Error in {step_name or error_context}**")
    
    error_type = error_response.get("error_type") or error_response.get("error", {}).get("type", "Unknown")
    error_code = error_response.get("code") or error_response.get("error", {}).get("code", "N/A")
    error_message = error_response.get("error_message") or error_response.get("error", {}).get("message", "No message")
    error_subcode = error_response.get("error_subcode") or error_response.get("error", {}).get("error_subcode")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Type:** `{error_type}`")
        st.markdown(f"**Code:** `{error_code}`")
        if error_subcode:
            st.markdown(f"**Subcode:** `{error_subcode}`")
    with col2:
        st.markdown(f"**Context:** {error_context}")
        st.markdown(f"**Time:** {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    
    st.code(error_message, language=None)
    with st.expander("🔍 Full Error JSON"):
        st.json(error_response)
    
    # Troubleshooting
    if "permission" in error_message.lower():
        st.warning("💡 **Permission Error**: Check required permissions were granted")
    elif "token" in error_message.lower() or "expired" in error_message.lower():
        st.warning("💡 **Token Error**: Token may have expired or been used already")
    elif "rate" in error_message.lower():
        st.warning("💡 **Rate Limit**: Wait before trying again")

def display_original_api_response(endpoint: str, method: str, response_data: dict, step_number: str):
    """Display original API response"""
    st.markdown(f"### 📡 Original API Response - Step {step_number}")
    st.markdown(f"**Endpoint:** `{method} {endpoint}`")
    
    json_str = json.dumps(response_data, indent=2, ensure_ascii=False)
    st.download_button(
        f"⬇️ Download Original Response Step {step_number}",
        json_str,
        f"original_response_step_{step_number}.json",
        "application/json",
        key=f"orig_resp_{step_number}"
    )
    
    with st.expander("📋 View Original API Response"):
        st.json(response_data)
    st.divider()

def check_permissions(granted_permissions: str):
    """Check if required permissions were granted"""
    granted = set(granted_permissions.split(',')) if granted_permissions else set()
    required = set(REQUIRED_PERMISSIONS.keys())
    missing = required - granted
    
    return {
        "granted": list(granted),
        "required": list(required),
        "missing": list(missing),
        "all_granted": len(missing) == 0
    }

def display_permission_check(permission_check: dict):
    """Display permission check results"""
    st.markdown("### 🔐 Permission Verification")
    
    if permission_check["all_granted"]:
        st.success("✅ All required permissions granted!")
    else:
        st.error("❌ Missing required permissions!")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Granted:**")
        for perm in permission_check["granted"]:
            icon = "🔑" if perm in permission_check["required"] else "➕"
            st.markdown(f"{icon} `{perm}`")
    
    with col2:
        st.markdown("**Required:**")
        for perm in permission_check["required"]:
            icon = "✅" if perm in permission_check["granted"] else "❌"
            desc = REQUIRED_PERMISSIONS.get(perm, "")
            st.markdown(f"{icon} `{perm}`")
            if desc and "CRITICAL" in desc:
                st.caption(f"⚠️ {desc}")
    
    if permission_check["missing"]:
        st.warning(f"""
        **Missing Permissions:** {', '.join([f'`{p}`' for p in permission_check["missing"]])}
        
        **Action Required:** Re-authorize and grant all permissions.
        """)
    st.divider()

# --- DISPLAY FUNCTIONS ----------------------------------------------------------
def display_api_endpoint_info(step_number, title, method, endpoint, description, params=None, body=None, notes=None):
    """Display API endpoint details"""
    st.markdown(f"### 🔌 API Endpoint - Step {step_number}")
    st.markdown(f"**{title}**")
    st.caption(description)
    
    method_color = "green" if method == "GET" else "blue"
    st.markdown(f"**Method:** :{method_color}[{method}]")
    st.code(endpoint, language=None)
    
    if params:
        with st.expander("📝 Request Parameters", expanded=True):
            for key, value in params.items():
                if key in ["client_secret", "access_token"] and value:
                    value = value[:20] + "..."
                st.code(f"{key}: {value}", language=None)
    
    if body:
        with st.expander("📦 Request Body"):
            st.json(body)
    
    if notes:
        with st.expander("📌 Important Notes"):
            for note in notes:
                st.markdown(f"- {note}")
    st.divider()

def display_json_with_download(title, purpose, data, filename, emoji="📄"):
    """Display JSON with download"""
    st.markdown(f"### {emoji} {title}")
    st.caption(f"**Purpose:** {purpose}")
    
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    st.download_button(
        f"⬇️ Download {filename}.json",
        json_str,
        f"{filename}.json",
        "application/json",
        key=f"dl_{filename}"
    )
    
    with st.expander("📋 View JSON Response"):
        st.json(data)
    st.divider()

# --- DATA PROCESSING FUNCTIONS --------------------------------------------------
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

def fetch_instagram_metrics(access_token, ig_user_id, days, followers):
    """Fetch engagement metrics for specified days"""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    fields = "id,timestamp,like_count,comments_count,insights.metric(views,impressions,reach,saved,shares,total_interactions)"
    url = f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media?fields={fields}&limit=50&access_token={access_token}"

    totals = {"likes": 0, "comments": 0, "shares": 0, "saves": 0, "reach": 0, "total_interactions": 0, "post_count": 0}
    all_posts = []

    while url:
        try:
            resp = requests.get(url, timeout=10).json()
            log_api_call(url.split('?')[0], "GET", resp)
            
            if "error" in resp:
                log_error(f"{days}-day metrics fetch", resp)
                display_api_error(f"{days}-day metrics", resp, f"{days}-Day Metrics")
                break
                
            if "data" not in resp:
                break

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
                
                all_posts.append(post)

            url = resp.get('paging', {}).get('next')
        except Exception as e:
            log_error(f"{days}-day metrics exception", {"error": str(e)})
            break

    engagement = totals["likes"] + totals["comments"] + totals["shares"] + totals["saves"]
    er = (engagement / followers * 100) if followers > 0 else 0
    
    return {"ER": round(er, 2), "posts": totals["post_count"], "totals": totals, "raw_posts": all_posts}

def fetch_media_totals(access_token, ig_user_id, days=90):
    """Fetch media totals"""
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        media_res = requests.get(
            f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}?fields=media_count&access_token={access_token}"
        ).json()
        log_api_call(f"/{ig_user_id}", "GET", media_res)
        
        if "error" in media_res:
            log_error("Media count fetch", media_res)
            display_api_error("Media count", media_res)
            return {"summary": {}, "media_items": []}
    except Exception as e:
        log_error("Media count exception", {"error": str(e)})
        return {"summary": {}, "media_items": []}
    
    media_count = media_res.get("media_count", 100)
    BASE_URL = (
        f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media?"
        f"fields=id,caption,media_type,media_product_type,timestamp,permalink,"
        f"like_count,comments_count,insights.metric(views,shares,saved)"
        f"&limit={min(100, media_count)}&access_token={access_token}"
    )

    totals = {"views": 0, "shares": 0, "saved": 0, "likes": 0, "comments": 0, "counted_media": 0, "skipped_old_media": 0}
    all_media = []

    next_url = BASE_URL
    while next_url:
        try:
            payload = requests.get(next_url).json()
            log_api_call(next_url.split('?')[0], "GET", payload)
            
            if "error" in payload:
                log_error("Media totals fetch", payload)
                display_api_error("Media totals", payload)
                break

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
                all_media.append(item)

            next_url = payload.get("paging", {}).get("next")
            time.sleep(0.1)
        except Exception as e:
            log_error("Media totals exception", {"error": str(e)})
            break

    return {"summary": totals, "media_items": all_media}

# --- STREAMLIT UI ---------------------------------------------------------------
st.set_page_config(page_title="Instagram Professional Insights", page_icon="📊", layout="wide")
st.title("📊 Instagram Professional Insights Suite")
st.caption("Complete OAuth Flow with API Documentation, Permission Checking & Error Handling")

query_params = st.query_params

if "code" not in query_params:
    st.info("👋 Please authorize your Instagram account to begin.")
    
    with st.expander("📖 How Instagram OAuth Works", expanded=True):
        st.markdown("""
        ### Instagram OAuth 2.0 Flow
        
        1. **Authorization Request** - User grants permissions
        2. **Authorization Code** - Instagram redirects with code (1-hour validity)
        3. **Token Exchange** - Exchange code for access token
        4. **API Access** - Use token to fetch data
        """)
        
        st.markdown("**Required Permissions:**")
        for perm, desc in REQUIRED_PERMISSIONS.items():
            st.markdown(f"- `{perm}` - {desc}")
    
    st.link_button("🚀 Login & Authorize Instagram", url=EMBED_URL, use_container_width=True)
    st.stop()

# ==================================================================================
# STEP 1: AUTHORIZATION CODE
# ==================================================================================
auth_code = query_params["code"]
if isinstance(auth_code, list):
    auth_code = auth_code[0]
auth_code = auth_code.split("#_")[0]

auth_code_response = {
    "authorization_code": auth_code,
    "received_at": datetime.now(timezone.utc).isoformat(),
    "source": "Instagram OAuth Redirect",
    "redirect_uri": INSTA_REDIRECT_URI,
    "valid_for": "1 hour",
    "single_use": True
}

st.success("✅ Authorization Code Received!")
print(f"\n✅ Authorization Code Received: {auth_code[:20]}...\n")

display_api_endpoint_info(
    "1", "OAuth Authorization Redirect", "GET",
    f"{INSTA_REDIRECT_URI}?code=...",
    "Instagram redirects with authorization code",
    params={"code": f"{auth_code[:30]}... (auth code)", "state": "(optional CSRF protection)"}
)

display_json_with_download(
    "1️⃣ Authorization Code",
    "Authorization code from OAuth redirect to exchange for access token",
    auth_code_response,
    "authorizationCode",
    "🔑"
)

# ==================================================================================
# PAUSE - WAIT FOR USER BUTTON CLICK
# ==================================================================================
st.markdown("---")
st.markdown("## ⏸️ Ready to Proceed?")

st.info("""
**👇 Click to start fetching all metrics**

This will:
- Exchange authorization code for tokens
- Verify permissions
- Fetch profile data and insights
- Calculate 7/30/90-day engagement metrics

⚠️ Takes 30-60 seconds. All API calls logged to terminal.
""")

if st.button("🚀 Go Ahead - Calculate All Metrics", type="primary", use_container_width=True):
    st.session_state.proceed_with_metrics = True
    st.rerun()

if not st.session_state.proceed_with_metrics:
    st.caption("💡 Review the authorization code above before proceeding.")
    st.stop()

# ==================================================================================
# API PROCESSING
# ==================================================================================
st.markdown("---")
st.markdown("## 🔄 API Request Flow")
st.success("✅ Processing started!")

with st.status("🔗 Processing...", expanded=True) as status:

    # STEP 2: SHORT-LIVED TOKEN
    st.write("🔄 Step 2: Exchanging for short-lived token...")
    token_url = "https://api.instagram.com/oauth/access_token"
    token_payload = {
        "client_id": INSTA_APP_ID,
        "client_secret": INSTA_APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": INSTA_REDIRECT_URI,
        "code": auth_code
    }
    
    try:
        token_res = requests.post(token_url, data=token_payload).json()
        log_api_call(token_url, "POST", token_res)
        
        if "error" in token_res or "error_type" in token_res:
            log_error("Token exchange", token_res)
            display_api_error("Token exchange", token_res, "Short-lived Token")
            st.stop()
    except Exception as e:
        error_data = {"error": str(e)}
        log_error("Token exchange exception", error_data)
        display_api_error("Token exchange", error_data)
        st.stop()
    
    # Handle response structure
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
        st.error("❌ No access token in response")
        st.json(token_res)
        st.stop()

    st.success("✅ Short-lived token received!")
    
    # STEP 3: LONG-LIVED TOKEN
    st.write("⬆️ Step 3: Upgrading to long-lived token...")
    ll_url = "https://graph.instagram.com/access_token"
    ll_params = {
        "grant_type": "ig_exchange_token",
        "client_secret": INSTA_APP_SECRET,
        "access_token": short_token
    }
    
    try:
        ll_res = requests.get(ll_url, params=ll_params).json()
        log_api_call(ll_url, "GET", ll_res)
        
        if "error" in ll_res:
            log_error("Long-lived token upgrade", ll_res)
            display_api_error("Token upgrade", ll_res, "Long-lived Token")
            st.stop()
    except Exception as e:
        error_data = {"error": str(e)}
        log_error("Long-lived token exception", error_data)
        display_api_error("Token upgrade", error_data)
        st.stop()
    
    access_token = ll_res.get("access_token")
    token_type = ll_res.get("token_type", "bearer")
    expires_in = ll_res.get("expires_in", 0)
    expiration_date = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    st.success("✅ Long-lived token received!")

    # STEP 4: BASE PROFILE
    st.write("👤 Step 4: Fetching base profile...")
    me_url = f"https://graph.instagram.com/{API_VERSION}/me?fields=id,user_id,username,name&access_token={access_token}"
    
    try:
        me_data = requests.get(me_url).json()
        log_api_call(me_url.split('?')[0], "GET", me_data)
        
        if "error" in me_data:
            log_error("Base profile fetch", me_data)
            display_api_error("Profile fetch", me_data, "Base Profile")
            st.stop()
    except Exception as e:
        error_data = {"error": str(e)}
        log_error("Base profile exception", error_data)
        display_api_error("Profile fetch", error_data)
        st.stop()
    
    app_id = me_data.get("id")
    ig_user_id = me_data.get("user_id")
    username = me_data.get("username")
    name = me_data.get("name")

    st.success("✅ Base profile received!")

    # STEP 5: PROFESSIONAL ACCOUNT
    st.write("📊 Step 5: Fetching professional account...")
    prof_url = f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}?fields=account_type,profile_picture_url,followers_count,follows_count,media_count&access_token={access_token}"
    
    try:
        prof_data = requests.get(prof_url).json()
        log_api_call(prof_url.split('?')[0], "GET", prof_data)
        
        if "error" in prof_data:
            log_error("Professional account fetch", prof_data)
            display_api_error("Professional account", prof_data, "Professional Account")
            st.stop()
    except Exception as e:
        error_data = {"error": str(e)}
        log_error("Professional account exception", error_data)
        display_api_error("Professional account", error_data)
        st.stop()

    account_type = prof_data.get("account_type")
    profile_pic = prof_data.get("profile_picture_url")
    followers = prof_data.get("followers_count", 0)
    follows = prof_data.get("follows_count", 0)
    media_count = prof_data.get("media_count", 0)

    st.success("✅ Professional account data received!")

    # STEPS 6-9: METRICS
    st.write("📈 Step 6: Fetching 7-day metrics...")
    report_7 = fetch_instagram_metrics(access_token, ig_user_id, 7, followers)
    st.success("✅ 7-Day metrics!")

    st.write("📈 Step 7: Fetching 30-day metrics...")
    report_30 = fetch_instagram_metrics(access_token, ig_user_id, 30, followers)
    st.success("✅ 30-Day metrics!")

    st.write("📈 Step 8: Fetching 90-day metrics...")
    report_90 = fetch_instagram_metrics(access_token, ig_user_id, 90, followers)
    st.success("✅ 90-Day metrics!")

    st.write("📸 Step 9: Fetching 90-day media totals...")
    media_totals = fetch_media_totals(access_token, ig_user_id, 90)
    st.success("✅ Media totals!")

    status.update(label="✅ All Data Loaded!", state="complete")

# ==================================================================================
# PERMISSION CHECK
# ==================================================================================
st.markdown("---")
permission_check = check_permissions(permissions)
display_permission_check(permission_check)

# ==================================================================================
# API DOCUMENTATION WITH ORIGINAL RESPONSES
# ==================================================================================
st.markdown("---")
st.markdown("## 📚 Complete API Documentation")

# STEP 2
display_original_api_response(token_url, "POST", token_res, "2")
display_api_endpoint_info(
    "2", "Exchange Authorization Code for Short-lived Token", "POST",
    token_url,
    "Exchange auth code for short-lived access token (~1 hour validity)",
    body=token_payload,
    notes=["Code is single-use only", "Returns access_token, user_id, permissions"]
)
display_json_with_download(
    "2️⃣ Short-lived Token Response",
    "Short-lived access token from code exchange",
    token_res,
    "shortLivedToken",
    "🕐"
)

# STEP 3
display_original_api_response(ll_url, "GET", ll_res, "3")
display_api_endpoint_info(
    "3", "Upgrade to Long-lived Token", "GET",
    ll_url,
    "Upgrade short-lived token to 60-day token",
    params=ll_params,
    notes=["Short-lived token must be valid", "Can be refreshed after 24 hours"]
)
display_json_with_download(
    "3️⃣ Long-lived Token Response",
    "Long-lived access token (60 days)",
    ll_res,
    "longLivedToken",
    "⏳"
)

# STEP 4
display_original_api_response(me_url.split('?')[0], "GET", me_data, "4")
display_api_endpoint_info(
    "4", "Get Base Profile", "GET",
    f"https://graph.instagram.com/{API_VERSION}/me",
    "Retrieve basic profile info",
    params={"fields": "id,user_id,username,name", "access_token": access_token},
    notes=["id = app-scoped ID", "user_id = Instagram Business Account ID"]
)
display_json_with_download(
    "4️⃣ Base Profile Response",
    "Basic profile information",
    me_data,
    "baseProfile",
    "👤"
)

# STEP 5
display_original_api_response(prof_url.split('?')[0], "GET", prof_data, "5")
display_api_endpoint_info(
    "5", "Get Professional Account", "GET",
    f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}",
    "Retrieve professional account details",
    params={"fields": "account_type,profile_picture_url,followers_count,follows_count,media_count", "access_token": access_token},
    notes=["Requires Business/Creator account", "account_type: BUSINESS or CREATOR"]
)
display_json_with_download(
    "5️⃣ Professional Account Response",
    "Professional account details",
    prof_data,
    "professionalAccount",
    "💼"
)

# INSIGHTS API DOCUMENTATION
st.markdown("---")
st.markdown("## 📊 Instagram Insights API Documentation")

with st.expander("📖 Insights API Overview", expanded=True):
    st.markdown("""
    ### What are Instagram Insights?
    
    Detailed analytics including engagement (likes, comments, shares, saves) and reach (impressions, reach, views).
    
    ### How to Request
    Use `insights.metric(metric_name1,metric_name2,...)` syntax in fields parameter.
    
    ### Available Metrics
    - **views**: Video views (Reels only)
    - **impressions**: Total times seen
    - **reach**: Unique accounts reached
    - **saved**: Times saved
    - **shares**: Times shared
    - **total_interactions**: Sum of engagement
    
    ### Requirements
    - `instagram_business_basic` permission (CRITICAL)
    - Business/Creator account
    - Data available for owned media only
    """)

# STEP 6-9: METRICS WITH INSIGHTS
media_fields = "id,timestamp,like_count,comments_count,insights.metric(views,impressions,reach,saved,shares,total_interactions)"

for day_period, report, step_num in [
    (7, report_7, "6"),
    (30, report_30, "7"),
    (90, report_90, "8")
]:
    display_api_endpoint_info(
        step_num,
        f"Get {day_period}-Day Engagement Metrics with Insights",
        "GET",
        f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media",
        f"Fetch {day_period}-day metrics with insights",
        params={"fields": media_fields, "limit": "50", "access_token": access_token},
        notes=[
            "insights.metric() requests specific metrics",
            "Pagination for large datasets",
            f"Filter by timestamp for {day_period}-day window"
        ]
    )
    
    display_json_with_download(
        f"{step_num}️⃣ {day_period}-Day Engagement Metrics",
        f"Engagement metrics for last {day_period} days with insights",
        report,
        f"{'seven' if day_period == 7 else 'thirty' if day_period == 30 else 'ninety'}DayMetrics",
        "📊"
    )

# STEP 9: MEDIA TOTALS
media_total_fields = "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count,insights.metric(views,shares,saved)"
display_api_endpoint_info(
    "9",
    "Get 90-Day Media Totals with Insights",
    "GET",
    f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media",
    "Complete media totals with metadata",
    params={"fields": media_total_fields, "limit": "100", "access_token": access_token},
    notes=["Includes media metadata", "Full permalinks", "Pagination handled"]
)

display_json_with_download(
    "9️⃣ 90-Day Media Totals",
    "Complete media totals with metadata",
    media_totals,
    "ninetyDayMediaTotals",
    "📸"
)

# ==================================================================================
# CALCULATIONS & FORMULAS
# ==================================================================================
st.markdown("---")
st.markdown("## 🧮 Insights Calculations & Formulas")

with st.expander("📐 Engagement Rate (ER) Calculation", expanded=True):
    st.markdown("""
    ### Formula
    ```
    ER = (Total Engagement / Followers) × 100
    Total Engagement = Likes + Comments + Shares + Saves
    ```
    
    ### Example
    ```python
    likes, comments, shares, saves = 150, 25, 30, 20
    followers = 5000
    
    total_engagement = 225
    ER = (225 / 5000) * 100 = 4.5%
    ```
    
    ### Benchmarks
    - Excellent: 3-6%
    - Good: 1-3%
    - Average: 0.5-1%
    - Low: <0.5%
    """)

with st.expander("📊 Extract Insights from Response"):
    st.markdown("""
    ```python
    def get_insight_value(media_item, metric_name):
        insights = media_item.get("insights", {}).get("data", [])
        for metric in insights:
            if metric.get("name") == metric_name:
                values = metric.get("values", [])
                if values:
                    return values[0].get("value", 0)
        return 0
    
    views = get_insight_value(post, "views")
    shares = get_insight_value(post, "shares")
    ```
    """)

# ==================================================================================
# DASHBOARD SUMMARY
# ==================================================================================
st.markdown("---")
st.markdown("## 📊 Dashboard Summary")

col1, col2 = st.columns([1, 4])
with col1:
    if profile_pic:
        st.image(profile_pic, width=120)
with col2:
    st.subheader(f"{name} (@{username})")
    st.write(f"**Account:** {account_type}")
    st.write(f"**Followers:** {followers:,}")
    st.write(f"**Following:** {follows:,}")
    st.write(f"**Media:** {media_count:,}")

st.divider()

st.markdown("### 🔐 Active Tokens")
token_col1, token_col2 = st.columns(2)
with token_col1:
    st.markdown("#### 🕐 Short-lived")
    st.code(short_token, language=None)
    st.caption("⏱️ ~1 hour")
with token_col2:
    st.markdown("#### ⏳ Long-lived")
    st.code(access_token, language=None)
    st.caption(f"⏱️ {expires_in // 86400} days")
    st.caption(f"📅 Expires: {expiration_date.strftime('%Y-%m-%d %H:%M:%S')}")

st.divider()

st.markdown("### 📈 Engagement Performance")
m1, m2, m3 = st.columns(3)
m1.metric("7-Day ER", f"{report_7['ER']}%", f"{report_7['posts']} posts")
m2.metric("30-Day ER", f"{report_30['ER']}%", f"{report_30['posts']} posts")
m3.metric("90-Day ER", f"{report_90['ER']}%", f"{report_90['posts']} posts")

st.divider()

st.markdown("### 📸 90-Day Media Summary")
summary = media_totals.get("summary", {})
cols = st.columns(5)
cols[0].metric("Views", f"{summary.get('views', 0):,}")
cols[1].metric("Shares", f"{summary.get('shares', 0):,}")
cols[2].metric("Saves", f"{summary.get('saved', 0):,}")
cols[3].metric("Likes", f"{summary.get('likes', 0):,}")
cols[4].metric("Comments", f"{summary.get('comments', 0):,}")

# ==================================================================================
# DEVELOPER RESOURCES
# ==================================================================================
st.markdown("---")
st.markdown("## 🎓 Developer Resources")

with st.expander("📖 Documentation Links"):
    st.markdown("""
    - [Instagram Platform](https://developers.facebook.com/docs/instagram-platform)
    - [Business Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login)
    - [Insights API](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/insights)
    - [Media Insights](https://developers.facebook.com/docs/instagram-platform/reference/ig-media/insights)
    """)

with st.expander("💻 Python Code Examples"):
    st.code("""
# Fetch media with insights
response = requests.get(
    f"https://graph.instagram.com/v24.0/{ig_user_id}/media",
    params={
        "fields": "id,insights.metric(impressions,reach,saved)",
        "access_token": token
    }
)

# Extract insights
for post in response.json().get("data", []):
    insights = post.get("insights", {}).get("data", [])
    for metric in insights:
        print(f"{metric['name']}: {metric['values'][0]['value']}")
    """, language="python")

st.success("🎉 All Data Loaded and Documented!")
