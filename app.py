import streamlit as st
import requests
import json
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import time

load_dotenv()

# Config
APP_ID = os.getenv("INSTAGRAM_APP_ID")  # e.g., "990602627938098"
APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET")
REDIRECT_URI = "https://your-streamlit-app.streamlit.app/redirect"  # Update to your URL
API_VERSION = "v24.0"
REQUIRED_PERMISSIONS = ["instagram_business_basic", "instagram_business_manage_insights"]  # ONLY these two!

st.set_page_config(page_title="Instagram Insights", layout="wide")

# Session state
if 'proceed_with_metrics' not in st.session_state:
    st.session_state.proceed_with_metrics = False
if 'auth_code' not in st.session_state:
    st.session_state.auth_code = None
if 'short_token_data' not in st.session_state:
    st.session_state.short_token_data = None
if 'permission_check' not in st.session_state:
    st.session_state.permission_check = None

def save_json_camelcase(data, filename):
    """Save JSON with camelCase filename."""
    camel_filename = filename.replace("_", "") + ".json"
    with open(camel_filename, "w") as f:
        json.dump(data, f, indent=2)
    return camel_filename

def log_to_terminal(message):
    """Log to terminal."""
    print(f"📡 API CALL - {datetime.now()}: {message}")

@st.cache_data(ttl=3600)
def exchange_auth_code(auth_code):
    """Exchange auth code for short-lived token."""
    token_url = "https://api.instagram.com/oauth/access_token"
    payload = {
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": auth_code
    }
    try:
        log_to_terminal(f"Method: POST | Endpoint: {token_url}")
        response = requests.post(token_url, data=payload).json()
        log_to_terminal(f"Status: SUCCESS | Response: {json.dumps(response, indent=2)}")
        return response
    except Exception as e:
        log_to_terminal(f"Status: ERROR | {str(e)}")
        st.error(f"Token exchange failed: {str(e)}")
        return None

def check_permissions(granted_permissions):
    """Check EXACTLY the two required permissions."""
    if isinstance(granted_permissions, list):
        granted = set(granted_permissions)
    elif isinstance(granted_permissions, str):
        granted = set(granted_permissions.split(","))
    else:
        granted = set()
    
    print(f"🔐 PERMISSION CHECK | Type: {type(granted_permissions)} | Granted: {granted}")
    
    missing = set(REQUIRED_PERMISSIONS) - granted
    extra = granted - set(REQUIRED_PERMISSIONS)
    is_valid = len(missing) == 0 and len(extra) == 0
    
    return {
        "granted": list(granted),
        "required": REQUIRED_PERMISSIONS,
        "missing": list(missing),
        "extra": list(extra),
        "is_valid": is_valid,
        "message": "✅ EXACT permissions match!" if is_valid else f"❌ Missing: {missing}, Extra: {extra}"
    }

def display_permission_check(check_result):
    """Display permission check."""
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Granted", ", ".join(check_result["granted"]))
    with col2:
        st.metric("Required", ", ".join(check_result["required"]))
    
    if check_result["is_valid"]:
        st.success("✅ Only required permissions present - proceeding!")
        st.balloons()
    else:
        st.error(check_result["message"])
        if st.button("🔄 Re-authorize (fix permissions)"):
            st.session_state.proceed_with_metrics = False
            st.session_state.auth_code = None
            st.session_state.short_token_data = None
            st.rerun()
        st.stop()

# Authorization URL
auth_url = f"https://api.instagram.com/oauth/authorize?client_id={APP_ID}&redirect_uri={REDIRECT_URI}&scope=instagram_business_basic,instagram_business_manage_insights&response_type=code"

# Main app
st.title("🚀 Instagram Token & Insights (Exact Permissions Check)")

# Step 1: Get auth code
query_params = st.query_params
auth_code = query_params.get("code")
if auth_code and not st.session_state.auth_code:
    st.session_state.auth_code = auth_code[0]
    st.success("✅ Auth code captured!")

if not st.session_state.auth_code:
    st.info("👆 Click to authorize (request ONLY the two required scopes)")
    if st.button("📱 Login & Authorize Instagram", use_container_width=True):
        st.query_params["next"] = "auth"
        st.markdown(f"[Authorize here]({auth_url})")
else:
    # Display auth code
    st.success("1️⃣ Authorization Code Received!")
    auth_data = {"code": st.session_state.auth_code, "timestamp": str(datetime.now())}
    st.json(auth_data)
    save_json_camelcase(auth_data, "authorizationCode")
    st.download_button("⬇️ Download authorizationCode.json", data=json.dumps(auth_data, indent=2), mime="application/json")
    
    # Pause button
    if not st.session_state.proceed_with_metrics:
        st.warning("⏸️ Review auth code above. Click to exchange for token & check permissions.")
        if st.button("🚀 Go Ahead - Exchange & Check Permissions", type="primary", use_container_width=True):
            with st.spinner("Exchanging code..."):
                st.session_state.short_token_data = exchange_auth_code(st.session_state.auth_code)
            if st.session_state.short_token_data:
                st.session_state.proceed_with_metrics = True
                st.rerun()
        st.stop()
    
    # Step 2: Short token + permissions (your exact format)
    if st.session_state.short_token_data:
        st.success("2️⃣ Short-lived Token (1hr) - api.instagram.com/oauth/access_token")
        st.json(st.session_state.short_token_data)  # Matches your response format
        token_file = save_json_camelcase(st.session_state.short_token_data, "shortLivedTokenResponse")
        st.download_button("⬇️ Download shortLivedTokenResponse.json", data=open(token_file, "r").read(), mime="application/json")
        
        permissions = st.session_state.short_token_data.get("permissions", [])
        st.session_state.permission_check = check_permissions(permissions)
        display_permission_check(st.session_state.permission_check)
        
        if not st.session_state.permission_check["is_valid"]:
            st.stop()

# Steps 3+: Long token, profile, insights (only if permissions exact)
if st.session_state.proceed_with_metrics and st.session_state.permission_check["is_valid"]:
    st.success("✅ Permissions validated - fetching data...")
    
    # Long-lived token (graph.instagram.com/access_token)
    short_token = st.session_state.short_token_data["access_token"]
    ll_url = "https://graph.instagram.com/access_token"
    ll_params = {"grant_type": "ig_exchange_token", "client_secret": APP_SECRET, "access_token": short_token}
    ll_response = requests.get(ll_url, params=ll_params).json()
    st.json(ll_response)
    save_json_camelcase(ll_response, "longLivedToken")
    
    # Profile, insights, etc. (abbreviated - full in your 1000+ line version)
    st.subheader("📊 Insights Dashboard (Full metrics here)")
    st.info("All 7/30/90-day metrics, ER calcs, media totals... (expand for full code)")

# Developer Docs (from PDF)
with st.expander("📚 Full API Docs & Insights Guide"):
    st.markdown("""
    - **OAuth**: POST https://api.instagram.com/oauth/access_token [file:1]
    - **Graph**: GET https://graph.instagram.com/v24.0/{id}/media?fields=insights.metric(impressions,reach) [file:1]
    - Required: ONLY instagram_business_basic, instagram_business_manage_insights [file:1]
    """)
