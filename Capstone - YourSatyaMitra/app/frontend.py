import streamlit as st
import requests
import json
import pandas as pd
import datetime
import base64
import sqlite3 
import os 

# CONFIG
API_URL = "http://localhost:8000/verify"
ANALYTICS_URL = "http://localhost:8000/analytics"
DELETE_URL = "http://localhost:8000/audit/delete"
DIAGRAM_PATH = "satyamitra_workflow.png" 

# Helper function to load analytics and enforce cache
def load_analytics_data(force_refresh=False):
    if 'analytics_cache' not in st.session_state or force_refresh:
        try:
            response = requests.get(ANALYTICS_URL)
            if response.status_code == 200:
                st.session_state['analytics_cache'] = response.json()
            else:
                return {"total_verifications": 0, "verdict_breakdown": {}, "recent_verifications": [], "origin_of_claim": [], "source_accuracy_breakdown": {}, "user_role_breakdown": {}, "hourly_counts": {}}
        except requests.exceptions.ConnectionError:
            return {"total_verifications": 0, "verdict_breakdown": {}, "recent_verifications": [], "origin_of_claim": [], "source_accuracy_breakdown": {}, "user_role_breakdown": {}, "hourly_counts": {}}
    return st.session_state['analytics_cache']

# Function to delete selected logs
def delete_selected_logs(selected_ids, user_role):
    try:
        response = requests.post(DELETE_URL, json={"ids": selected_ids, "user_role": user_role})
        if response.status_code == 200:
            st.success(f"Deletion successful: {response.json()['message']}")
            
            # Auto-refresh logic: Clear cache and force rerun
            if 'analytics_cache' in st.session_state:
                del st.session_state['analytics_cache']
            st.rerun() 
            
        else:
            st.error(f"Deletion failed. Server Response: {response.json().get('detail', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        st.error("Connection failed during delete operation.")

# 1. Page Config
st.set_page_config(
    page_title="SatyaMitra Enterprise", 
    page_icon="⚖️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLING & HEADERS ---
st.markdown(
    """
    <style>
    .main-header {font-size: 2.5rem; font-weight: 700; text-align: center; margin-bottom: 0;}
    .sub-header {font-size: 1.2rem; text-align: center; color: #666; margin-top: -10px;}
    
    :root {
        --primary-color: #B19CD9;
        --primary-background-color: #B19CD9;
    }

    .stAlert.info {
        background-color: #E6E6FA; color: #4B0082; border-color: #B19CD9;
    }
    .stAlert.info div[data-testid="stMarkdownContainer"] {
        color: #4B0082;
    }
    
    /* === ROBUST BUTTON STYLING FOR RED (Enabled/Disabled) === */
    div[data-testid="stVerticalBlock"] div.stButton button {
        background-color: #FF4B4B !important; 
        border-color: #FF4B4B !important;
        color: white !important; 
        opacity: 1; 
    }
    
    div[data-testid="stVerticalBlock"] div.stButton button:disabled {
        background-color: #FF4B4B !important; 
        border-color: #FF4B4B !important;
        color: white !important;
        opacity: 0.5 !important; 
    }

    div[data-testid="stVerticalBlock"] div.stButton button:hover:not(:disabled) { 
        background-color: #E03C3C !important; 
        border-color: #E03C3C !important;
        color: white !important;
    }
    
    iframe {
        border: 1px solid #333;
        border-radius: 8px;
    }
    </style>
    """, unsafe_allow_html=True
)

# --- HEADER ---
st.markdown(
    """
    <h1 class='main-header'>
        ⚖️ 
        <span style='background: linear-gradient(to right, #FF9933, #FFFFFF, #138808); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>
            SatyaMitra
        </span>
    </h1>
    <p class='sub-header'>Enterprise-Grade Misinformation Detection System</p>
    """, 
    unsafe_allow_html=True
)

# --- GLOBAL SESSION STATE INITIALIZATION ---
if 'user_role' not in st.session_state: st.session_state['user_role'] = 'admin'
if 'user_id' not in st.session_state: st.session_state['user_id'] = 'admin_01'
if 'last_report_summary' not in st.session_state: st.session_state['last_report_summary'] = None
if 'last_full_report' not in st.session_state: st.session_state['last_full_report'] = None
if 'last_trace' not in st.session_state: st.session_state['last_trace'] = []
if 'report_ready' not in st.session_state: st.session_state['report_ready'] = False 


# --- SIDEBAR: SETTINGS & PROFILE ---
with st.sidebar:
    st.header("⚙️ Configuration")
    
    user_options = {
        'Senior Analyst (Admin)': 'admin',
        'Standard User (Auditor)': 'standard'
    }
    selected_role_label = st.selectbox("Select User Role:", list(user_options.keys()))
    
    if user_options[selected_role_label] == 'admin':
        st.session_state['user_role'] = 'admin'
        st.session_state['user_id'] = 'admin_01'
        st.success("Admin role grants database write permission.")
    else:
        st.session_state['user_role'] = 'standard'
        st.session_state['user_id'] = 'standard_02'
        st.warning("Standard role is read-only for security.")

    with st.expander("👤 Current Session", expanded=True):
        st.write(f"**User:** {st.session_state['user_id']}")
        st.write(f"**Role:** {st.session_state['user_role'].title()}")
        st.write("**Session:** Active")
    
    st.subheader("🤖 Model Settings")
    model_choice = st.selectbox("Select Reasoning Engine", ["Gemini 2.5 Flash (Default)", "Gemini 1.5 Pro", "GPT-4o (External)"])
    strictness = st.slider("Strictness Level", 0, 100, 75, help="Higher strictness requires more primary sources.")
    
    st.subheader("📡 System Status")
    st.success("API: Online")
    st.info("MCP Memory: Connected")
    st.warning("WhatsApp Stream: Active") 

# --- MAIN TABS ---
tab1, tab2, tab3, tab4 = st.tabs(["🕵️ Live Investigation", "📂 Batch Processing", "📊 Analytics Dashboard", "📜 Audit Logs"])

# === TAB 1: LIVE INVESTIGATION ===
with tab1:
    col1, col2 = st.columns([2, 1])
    
    with col2:
        st.subheader("Flow Visualization")
        
        if st.session_state.get('user_role') == 'admin':
            st.markdown("##### Agent Logic Workflow (Admin View)")
            
            if os.path.exists(DIAGRAM_PATH):
                st.image(DIAGRAM_PATH, use_container_width=True, caption="LangGraph Architecture")
            else:
                 st.error("Diagram file not found. Ensure the image is saved as satyamitra_workflow.png in this directory.")
        else:
            st.markdown("##### Agent Logic Workflow")
            st.info("Workflow diagram access is restricted to Senior Analysts (Admin role) for security purposes.")
            
        st.markdown("---")
        st.subheader("🧠 Context & Memory")
        st.info("Similar claims found in database: 0") 
        st.markdown("### 🌍 Real-time Threat Map")
        st.map(pd.DataFrame({'lat': [12.97, 28.70, 19.07], 'lon': [77.59, 77.10, 72.87]}))
        st.caption("Active disinformation nodes detected.")

    with col1:
        st.subheader("Single Claim Verification")
        
        input_mode = st.radio("Input Type:", ["Text / Claim", "Web Page URL", "Image Analysis"], horizontal=True)
        
        user_input = ""
        image_data = None
        input_type_val = "text"
        submit_enabled = False
        
        if input_mode == "Text / Claim":
            deep_search = st.checkbox("Enable Deep Web Search", value=True) 
            user_input = st.text_area("", height=150, placeholder="Paste suspicious text or social media post:")
            input_type_val = "text"
            if user_input.strip(): submit_enabled = True
        
        elif input_mode == "Web Page URL":
            user_input = st.text_input("Paste article URL:", placeholder="https://example.com/news-story")
            input_type_val = "url"
            deep_search = st.checkbox("Enable Deep Web Search", value=True)
            if user_input.strip(): submit_enabled = True
            
        elif input_mode == "Image Analysis":
            uploaded_file = st.file_uploader("Upload an image to verify (Fake/Edited check)", type=["jpg", "png", "jpeg"])
            input_type_val = "image"
            
            if uploaded_file is not None:
                st.image(uploaded_file, caption="Uploaded Image", width=300)
                bytes_data = uploaded_file.getvalue()
                b64_string = base64.b64encode(bytes_data).decode()
                image_data = f"data:{uploaded_file.type};base64,{b64_string}"
                user_input = "Analyze this image for manipulation or fake news context."
                submit_enabled = True
        
        col_btn, col_opt = st.columns([1, 2])
        with col_btn:
            run_btn = st.button("🔍 Investigate", type="primary", disabled=not submit_enabled)
        with col_opt:
            # --- NEW REFRESH BUTTON ADDED ---
            def clear_session_results():
                """Resets all result-related session state variables."""
                st.session_state['last_report_summary'] = None
                st.session_state['last_full_report'] = None
                st.session_state['last_trace'] = []
                st.session_state['report_ready'] = False

            st.button("🔄 Clear Results", on_click=clear_session_results, key="clear_live_results")
            # --------------------------------

        if run_btn:
            # Clear persistent data from previous run when a new investigation starts
            st.session_state['last_report_summary'] = None
            st.session_state['last_full_report'] = None
            st.session_state['last_trace'] = []
            st.session_state['report_ready'] = False # Reset flag
            
            status_box = st.status("🕵️ Dispatching Agents...", expanded=True)
            
            try:
                payload = {
                    "text": user_input, 
                    "user_id": st.session_state['user_id'],
                    "input_type": input_type_val,
                    "image_data": image_data,
                    "user_role": st.session_state['user_role'] 
                }
                
                with requests.post(API_URL, json=payload, stream=True) as response:
                    
                    final_verdict = None
                    current_trace = []

                    if response.status_code != 200:
                        status_box.update(label="❌ Server Error", state="error")
                        st.error(f"Server Error: {response.status_code}")
                        st.write(response.text)
                    else:
                        for line in response.iter_lines():
                            if line:
                                try:
                                    data = json.loads(line)
                                    
                                    if data["type"] == "step":
                                        status_box.write(f"**{data.get('status', 'Processing')}**")
                                        current_trace.append(data.get("details", "")) 
                                        
                                    elif data["type"] == "result":
                                        final_verdict = data["verdict"]
                                        status_box.update(label="✅ Investigation Complete", state="complete", expanded=False)
                                
                                except json.JSONDecodeError:
                                    continue

                        if final_verdict:
                            if "---DETAILED_REPORT_START---" in final_verdict:
                                parts = final_verdict.split("---DETAILED_REPORT_START---")
                                st.session_state['last_report_summary'] = parts[0].strip()
                                st.session_state['last_full_report'] = parts[1].strip()
                            else:
                                st.session_state['last_report_summary'] = final_verdict
                                st.session_state['last_full_report'] = final_verdict
                            
                            st.session_state['last_trace'] = current_trace
                            st.session_state['report_ready'] = True
                            
                            st.rerun()

            except Exception as e:
                status_box.update(label="❌ Connection Failed", state="error")
                st.error(f"Error connecting to backend: {e}")

        if st.session_state.get('report_ready'):
            st.success("Veracity Assessment Complete")
            
            with st.container(border=True):
                st.markdown("### 📋 Verdict Summary")
                st.markdown(st.session_state['last_report_summary'])
                st.caption(f"Analysis Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            st.download_button(
                label="📄 Download Detailed Investigation Report (TXT)", 
                data=st.session_state['last_full_report'], 
                file_name="SatyaMitra_Report.txt",
                mime="text/plain",
                use_container_width=True
            )
            
            with st.expander("🧩 View Agent Reasoning Trace (Audit)"):
                st.json(st.session_state['last_trace'])

# === TAB 2: BATCH PROCESSING (Mock) ===
with tab2:
    st.subheader("📂 Bulk Verification")
    uploaded_file = st.file_uploader("Upload dataset", type=["csv", "xlsx"])
    if uploaded_file:
        st.success("File uploaded successfully. 142 records detected.")
        st.dataframe(pd.DataFrame({
            "Claim ID": [101, 102, 103],
            "Text": ["Aliens in Egypt", "Moon landing fake", "Earth is flat"],
            "Status": ["Pending", "Pending", "Pending"]
        }))
        st.button("Start Batch Job")

# === TAB 3: ANALYTICS (Real Data) ===
with tab3:
    st.subheader("📊 Disinformation Trends")
    
    analytics_data = load_analytics_data()
    total = analytics_data.get("total_verifications", 0)
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Verifications", f"{total:,}", help="Total investigations logged in the database.")
    m2.metric("Trust Score", "N/A", help="Requires dedicated evaluation metrics.")
    m3.metric("Avg Response Time", "N/A", help="Requires server-side timing log.")
    
    st.markdown("---") 

    # --- 1. UPDATED VERDICT BREAKDOWN (Fixed and Smaller) ---
    verdict_breakdown = analytics_data.get("verdict_breakdown", {})
    
    standard_verdicts = {"TRUE": 0, "FALSE": 0, "MISLEADING": 0, "UNVERIFIED": 0}
    for verdict_type in standard_verdicts:
        if verdict_type in verdict_breakdown:
            standard_verdicts[verdict_type] = verdict_breakdown[verdict_type]
            
    df_chart = pd.DataFrame(standard_verdicts.items(), columns=["Verdict", "Count"])
    
    st.markdown("##### 📉 Verdict Breakdown (All Types)")
    st.bar_chart(df_chart, x="Verdict", y="Count", height=300, use_container_width=True) 
    
    st.markdown("---") 

    col_origin, col_user = st.columns(2)

    # --- 2. NEW METRIC: ORIGIN OF CLAIM (City, Country, Count) ---
    with col_origin:
        st.markdown("##### 📍 Origin of Claims (City, Country)")
        origin_of_claim_list = analytics_data.get("origin_of_claim", [])
        
        if origin_of_claim_list:
            df_origin = pd.DataFrame([
                {"City": item['city'], "Country": item['country'], "Count": item['count']}
                for item in origin_of_claim_list
            ])
            st.dataframe(df_origin, use_container_width=True, hide_index=True)
        else:
            st.info("No claim origin data available yet.")

    # --- 3. NEW METRIC: USER ROLE BREAKDOWN ---
    with col_user:
        st.markdown("##### 🧑‍💻 User Role Breakdown")
        user_role_breakdown = analytics_data.get("user_role_breakdown", {})
        if user_role_breakdown:
            df_user_role = pd.DataFrame(user_role_breakdown.items(), columns=["Role", "Count"])
            st.bar_chart(df_user_role, x="Role", y="Count", height=250)
        else:
            st.info("No user role data available yet.")
        
    st.markdown("---") 

    # --- 4. NEW METRIC: SOURCE ACCURACY & TRUSTWORTHY SOURCE ---
    st.markdown("##### 📊 Source Accuracy vs. Verdict")
    source_accuracy_breakdown = analytics_data.get("source_accuracy_breakdown", {})

    if source_accuracy_breakdown:
        source_data = []
        true_verdicts_by_source = {}
        
        # Flatten the nested dictionary for the table display
        for source, verdicts in source_accuracy_breakdown.items():
            true_count = verdicts.get("TRUE", 0)
            false_count = verdicts.get("FALSE", 0)
            unverified_count = verdicts.get("UNVERIFIED", 0)
            total_count = sum(verdicts.values())
            
            true_verdicts_by_source[source] = true_count

            source_data.append({
                "Source": source,
                "Total Claims": total_count,
                "TRUE": true_count,
                "FALSE": false_count,
                "UNVERIFIED": unverified_count,
            })
        
        df_source = pd.DataFrame(source_data)
        
        st.dataframe(df_source.sort_values(by="Total Claims", ascending=False), use_container_width=True, hide_index=True)

        if true_verdicts_by_source:
            most_trustworthy_source = max(true_verdicts_by_source, key=true_verdicts_by_source.get)
            st.metric("🥇 Most Trustworthy Source (by TRUE verdicts)", 
                     f"{most_trustworthy_source}", 
                     delta=f"{true_verdicts_by_source[most_trustworthy_source]} TRUE verdicts")
        else:
            st.info("Not enough data to determine a most trustworthy source yet.")

    else:
        st.info("No source accuracy data available yet.")
        
    st.markdown("---") 

    # --- 5. PEAK USAGE HOURS ---
    st.markdown("##### ⏱️ Peak Usage Hours")
    hourly_counts = analytics_data.get("hourly_counts", {})
    if hourly_counts:
        # Pad hours from 00 to 23 for continuous display, set missing counts to 0
        df_hourly = pd.DataFrame([
            {'Hour': f"{h:02d}:00", 'Count': hourly_counts.get(f"{h:02d}", 0)} 
            for h in range(24)
        ])
        st.bar_chart(df_hourly, x="Hour", y="Count", height=250, use_container_width=True)
    else:
        st.info("No hourly usage data available yet.")


# === TAB 4: AUDIT LOGS (Real Data - Table View) ===
with tab4:
    st.subheader("📜 System Audit Logs")
    
    # --- MANUAL REFRESH BUTTON ADDED HERE ---
    col_header, col_refresh = st.columns([4, 1])
    col_header.markdown("#### Verification History")
    
    def manual_refresh():
        if 'analytics_cache' in st.session_state:
            del st.session_state['analytics_cache']
        
    if col_refresh.button("🔄 Refresh Data", on_click=manual_refresh):
        pass
    
    analytics_data = load_analytics_data() 
    recent_verifications = analytics_data.get("recent_verifications", [])
    
    df_audit_data = [
        {'ID': item.get('id', 'N/A'), 
         'User ID': item.get('user_id', 'N/A'),
         'Claim': item.get('claim_text', 'N/A'), 
         'Verdict': item.get('verdict', 'N/A'), 
         'Timestamp': item.get('timestamp', 'N/A')[:19]}
        for item in recent_verifications
    ]
    df_audit = pd.DataFrame(df_audit_data)

    if not df_audit.empty:
        df_audit.insert(0, 'Select', False)
        
        edited_df = st.data_editor(
            df_audit,
            column_config={
                'ID': st.column_config.Column(disabled=True),
                'User ID': st.column_config.Column(width="small", help="The user who submitted the claim (e.g., admin_01)."),
                'Claim': st.column_config.Column(width="medium", help="The full text of the claim analyzed."),
                'Verdict': st.column_config.Column(width="small"), 
                'Timestamp': st.column_config.Column(width="small"),
                'Select': st.column_config.CheckboxColumn(default=False)
            },
            hide_index=True,
            num_rows="dynamic",
            use_container_width=True
        )
        
        selected_ids_from_df = edited_df[edited_df['Select']]['ID'].tolist()
        
        st.markdown("---")
        
        col_del, col_space = st.columns([1, 3])
        with col_del:
            delete_disabled = st.session_state['user_role'] != 'admin' or not selected_ids_from_df
            
            if st.button("🗑️ Delete Selected Logs", disabled=delete_disabled):
                delete_selected_logs(selected_ids_from_df, st.session_state['user_role'])
        
        with col_space:
             if st.session_state['user_role'] != 'admin':
                st.error("Admin required to delete logs.")
             elif selected_ids_from_df:
                 st.info(f"{len(selected_ids_from_df)} log(s) selected for deletion.")

    else:
        st.info("No verification history found yet.")

# --- FOOTER ---
st.markdown("---")
st.markdown("<div style='text-align: center; color: grey;'>SatyaMitra Enterprise v2.0 | Powered by LangGraph & Gemini | Confidential</div>", unsafe_allow_html=True)