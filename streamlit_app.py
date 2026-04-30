import streamlit as st
import pandas as pd
import snowflake.connector
import json

st.set_page_config(page_title="MA Intelligence Hub", page_icon="🎯", layout="wide")

# ── CONNECTION ────────────────────────────────────────────────────────────────
@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account   = st.secrets["snowflake"]["account"],
        user      = st.secrets["snowflake"]["user"],
        password  = st.secrets["snowflake"]["password"],
        warehouse = st.secrets["snowflake"]["warehouse"],
        database  = st.secrets["snowflake"]["database"],
        schema    = st.secrets["snowflake"]["schema"],
        client_session_keep_alive=True,
    )

def get_cursor():
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("SELECT 1")
        return cur
    except Exception as e:
        if "390114" in str(e) or "expired" in str(e).lower():
            get_connection.clear()
            return get_connection().cursor()
        raise e

@st.cache_data(ttl=3600)
def run_query(sql):
    cur = get_cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=cols)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
LARGE_NATIONALS = [
    "HUMANA","UNITED","UNITEDHEALTHCARE","AETNA","CVS","CENTENE","MOLINA",
    "ANTHEM","ELEVANCE","BCBS","BLUE CROSS","BLUE SHIELD",
    "HEALTH CARE SERVICE","HCSC","KAISER","CIGNA","WELLCARE","MOLINA",
    "DEVOTED","OSCAR","BRIGHT HEALTH"
]

LN_FILTER_V = " AND ".join([f"UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%{n}%'" for n in LARGE_NATIONALS])
LN_FILTER_C = " AND ".join([f"UPPER(C.Parent_Organization_Name) NOT LIKE '%{n}%'" for n in LARGE_NATIONALS])

CONSULTING_FIT_V = """CASE
    WHEN (UPPER(V.PARENT_ORGANIZATION) LIKE '%HUMANA%' OR UPPER(V.PARENT_ORGANIZATION) LIKE '%UNITED%'
       OR UPPER(V.PARENT_ORGANIZATION) LIKE '%AETNA%'  OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CVS%'
       OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CENTENE%' OR UPPER(V.PARENT_ORGANIZATION) LIKE '%MOLINA%'
       OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ANTHEM%'  OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ELEVANCE%'
       OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BCBS%'    OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BLUE CROSS%'
       OR UPPER(V.PARENT_ORGANIZATION) LIKE '%KAISER%'  OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CIGNA%'
       OR UPPER(V.PARENT_ORGANIZATION) LIKE '%WELLCARE%') THEN 'Large National'
    WHEN TRY_TO_NUMBER(V.MBR_CNT) > 150000 THEN 'Large - Has Team'
    WHEN TRY_TO_NUMBER(V.MBR_CNT) > 50000  THEN 'Mid-Size - Maybe'
    ELSE 'Small/Regional - TARGET'
END"""

SYSTEM_CONTEXT = """You are an expert Medicare Advantage consulting analyst for Sadaf, who runs a SMALL BOUTIQUE consulting firm.

KEY FACTS:
- Solo/small firm — she can realistically take on 2-5 clients max
- Target: small/regional MA plans under 50K members with NO in-house analytics team
- Value prop: star ratings improvement, risk adjustment analytics, compliance strategy
- NEVER recommend plans owned by: Humana, United, Aetna, CVS, Centene, Molina, Anthem/Elevance, BCBS, Kaiser, Cigna, WellCare

When answering questions about outreach targets:
1. Lead with TOP 3 recommendations only (she is small — focus matters)
2. For each: Plan Name, Contract ID, State, Enrollment, Stars, Contact Name + Email, why they need help
3. End with a one-line cold outreach email subject line for each
4. Be direct and actionable — Sadaf has data expertise and wants concrete next steps"""

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🎯 MA Intelligence Hub")
    st.caption("Sadaf's consulting opportunity engine")
    st.divider()
    st.subheader("Filters")
    target_only    = st.checkbox("Targets Only (hide large nationals)", value=True)
    max_enrollment = st.number_input("Max Enrollment", 0, 1000000, 150000, 10000)
    cap_only       = st.checkbox("CAP Plans Only")
    lpi_only       = st.checkbox("Low Performers Only")
    state_list     = st.multiselect("State", ["All","AL","AK","AZ","AR","CA","CO","CT","DE","FL",
                                               "GA","HI","ID","IL","IN","IA","KS","KY","LA","ME",
                                               "MD","MA","MI","MN","MS","MO","MT","NE","NV","NH",
                                               "NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI",
                                               "SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"],
                                    default=["All"])

def where_clause():
    w = []
    if target_only:
        w.append(LN_FILTER_V)
    if max_enrollment > 0:
        w.append(f"TRY_TO_NUMBER(V.MBR_CNT) <= {max_enrollment}")
    if cap_only:
        w.append("V.Issue_Type IS NOT NULL")
    if lpi_only:
        w.append("V.REASON_FOR_LPI IS NOT NULL")
    if state_list and "All" not in state_list:
        s = ",".join([f"'{x}'" for x in state_list])
        w.append(f"V.LEGAL_ENTITY_STATE_CODE IN ({s})")
    return ("WHERE " + " AND ".join(w)) if w else ""

# ── TABS ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["🎯 Opportunities", "📋 CAP Actions", "⭐ Star Ratings", "🤖 AI Assistant"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OPPORTUNITIES
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("🎯 Consulting Opportunities")
    st.caption("Ranked by opportunity score. CAP contact shown first, then directory contact.")

    wh = where_clause()

    SQL = f"""
    SELECT
        V.CONTRACT_ID,
        V.ORGANIZATION_MARKETING_NAME                                       AS PLAN_NAME,
        V.PARENT_ORGANIZATION,
        V.LEGAL_ENTITY_STATE_CODE                                           AS STATE,
        V.PLAN_TYPE,
        TRY_TO_NUMBER(V.MBR_CNT)                                           AS ENROLLMENT,
        V."2026_OVERALL"                                                    AS OVERALL_STARS,
        V."2026_PART_C_SUMMARY"                                             AS PART_C_STARS,
        V."2026_PART_D_SUMMARY"                                             AS PART_D_STARS,
        V.OPPORTUNITY_SCORE,
        COALESCE(C.RECIPIENT_NAME,     V.Organization_Contact_Name)        AS CONTACT_NAME,
        COALESCE(C.EMAIL,              V.DIRECTORY_CONTACT_EMAIL)          AS CONTACT_EMAIL,
        COALESCE(V.Organization_Contact_Phone, V.DIRECTORY_CONTACT_PHONE)  AS CONTACT_PHONE,
        V.REASON_FOR_LPI,
        V.OVERALL_FAC                                                       AS CAI_FLAG,
        COALESCE(C.Issue_Type,  V.Issue_Type)                              AS CAP_ISSUE_TYPE,
        COALESCE(C.Issue_Topic, '')                                         AS CAP_ISSUE_TOPIC,
        {CONSULTING_FIT_V}                                                  AS CONSULTING_FIT
    FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
    LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
        ON TRIM(V.CONTRACT_ID) = TRIM(C.CONTRACT_ID)
    {wh}
    ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST, TRY_TO_NUMBER(V.MBR_CNT) ASC NULLS LAST
    LIMIT 300
    """

    c1, c2, c3 = st.columns([2,1,1])
    min_score = c1.slider("Min Opportunity Score", 0, 70, 0, 5)
    show_n    = c2.selectbox("Show top", [25, 50, 100, 200], 0)
    c3.write("")
    run_opp   = c3.button("🔍 Find Opportunities", type="primary", use_container_width=True)

    if run_opp or "opp_df" not in st.session_state:
        with st.spinner("Loading from Snowflake..."):
            try:
                df = run_query(SQL)
                if min_score > 0:
                    df = df[pd.to_numeric(df["OPPORTUNITY_SCORE"], errors="coerce") >= min_score]
                st.session_state["opp_df"] = df.head(show_n)
            except Exception as e:
                st.error(f"Query error: {e}")

    if "opp_df" in st.session_state:
        df = st.session_state["opp_df"]
        targets = df[df["CONSULTING_FIT"] == "Small/Regional - TARGET"]
        maybe   = df[df["CONSULTING_FIT"] == "Mid-Size - Maybe"]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Plans Found", len(df))
        m2.metric("Prime Targets", len(targets))
        m3.metric("Mid-Size Maybe", len(maybe))
        scores = pd.to_numeric(df["OPPORTUNITY_SCORE"], errors="coerce")
        m4.metric("Avg Opp Score", f"{scores.mean():.0f}" if scores.notna().any() else "N/A")

        # Table
        show_cols = ["CONTRACT_ID","PLAN_NAME","STATE","ENROLLMENT","OVERALL_STARS",
                     "OPPORTUNITY_SCORE","CONSULTING_FIT","CONTACT_NAME","CONTACT_EMAIL",
                     "CAP_ISSUE_TYPE","REASON_FOR_LPI","CAI_FLAG"]
        show_cols = [c for c in show_cols if c in df.columns]
        st.dataframe(df[show_cols], use_container_width=True, height=400)

        # Detail card
        st.subheader("📋 Plan Detail Card")
        selected = st.selectbox("Select plan", df["PLAN_NAME"].tolist(), key="opp_sel")
        if selected:
            row = df[df["PLAN_NAME"] == selected].iloc[0]
            fit = row.get("CONSULTING_FIT","")
            color = "#e8f5e9" if "TARGET" in fit else "#fff9c4" if "Maybe" in fit else "#ffebee"
            st.markdown(f"""
            <div style="background:{color};padding:16px;border-radius:8px;margin-bottom:8px">
            <h4>{row.get('PLAN_NAME','')} ({row.get('CONTRACT_ID','')})</h4>
            <b>Parent:</b> {row.get('PARENT_ORGANIZATION','')} &nbsp;|&nbsp;
            <b>State:</b> {row.get('STATE','')} &nbsp;|&nbsp;
            <b>Enrollment:</b> {row.get('ENROLLMENT','')} &nbsp;|&nbsp;
            <b>Fit:</b> {fit}
            </div>""", unsafe_allow_html=True)

            r1, r2, r3 = st.columns(3)
            with r1:
                st.markdown("**⭐ Star Ratings**")
                st.markdown(f"Overall: **{row.get('OVERALL_STARS','N/A')}**")
                st.markdown(f"Part C: {row.get('PART_C_STARS','N/A')}")
                st.markdown(f"Part D: {row.get('PART_D_STARS','N/A')}")
                st.markdown(f"Opp Score: **{row.get('OPPORTUNITY_SCORE','N/A')}**")
            with r2:
                st.markdown("**⚠️ Compliance Flags**")
                st.markdown(f"CAP Issue: {row.get('CAP_ISSUE_TYPE','None')}")
                st.markdown(f"CAP Topic: {row.get('CAP_ISSUE_TOPIC','None')}")
                st.markdown(f"Low Performer: {row.get('REASON_FOR_LPI','No')}")
                st.markdown(f"CAI Flag: {row.get('CAI_FLAG','No')}")
            with r3:
                st.markdown("**📞 Contact**")
                st.markdown(f"Name: **{row.get('CONTACT_NAME','N/A')}**")
                st.markdown(f"Email: {row.get('CONTACT_EMAIL','N/A')}")
                st.markdown(f"Phone: {row.get('CONTACT_PHONE','N/A')}")

        st.download_button("⬇️ Download CSV", df.to_csv(index=False), "opportunities.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CAP ACTIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("📋 CAP Enforcement Actions")
    st.caption("CMS corrective actions — these contacts are warm leads for compliance consulting")

    cap_sql = f"""
    SELECT
        C.CONTRACT_ID,
        C.ORGANIZATION_MARKETING_NAME                   AS PLAN_NAME,
        C.Parent_Organization_Name                      AS PARENT_ORG,
        C.LEGAL_ENTITY_STATE_CODE                       AS STATE,
        TRY_TO_NUMBER(C.MBR_CNT)                       AS ENROLLMENT,
        C.RECIPIENT_NAME,
        C.EMAIL,
        C.Issue_Type,
        C.Issue_Topic,
        V."2026_OVERALL"                                AS OVERALL_STARS,
        V.OPPORTUNITY_SCORE,
        {CONSULTING_FIT_V}                              AS CONSULTING_FIT
    FROM MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
    LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
        ON TRIM(C.CONTRACT_ID) = TRIM(V.CONTRACT_ID)
    ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST
    LIMIT 500
    """

    if st.button("Load CAP Data", type="primary", key="load_cap"):
        with st.spinner("Loading..."):
            try:
                st.session_state["cap_df"] = run_query(cap_sql)
            except Exception as e:
                st.error(f"{e}")

    if "cap_df" in st.session_state:
        cap_df = st.session_state["cap_df"]

        f1, f2 = st.columns(2)
        it_options = ["All"] + sorted(cap_df["ISSUE_TYPE"].dropna().unique().tolist()) if "ISSUE_TYPE" in cap_df.columns else ["All"]
        sel_it  = f1.selectbox("Issue Type", it_options)
        show_ln = f2.checkbox("Show large nationals", value=False)

        view = cap_df.copy()
        if sel_it != "All" and "ISSUE_TYPE" in view.columns:
            view = view[view["ISSUE_TYPE"] == sel_it]
        if not show_ln:
            for n in LARGE_NATIONALS:
                view = view[~view["PARENT_ORG"].str.upper().str.contains(n, na=False)]

        st.metric("CAP Actions", len(view))
        show_cap_cols = [c for c in ["CONTRACT_ID","PLAN_NAME","STATE","ENROLLMENT",
                                      "Issue_Type","Issue_Topic","RECIPIENT_NAME","EMAIL",
                                      "OVERALL_STARS","CONSULTING_FIT"] if c in view.columns]
        st.dataframe(view[show_cap_cols], use_container_width=True, height=400)

        # Detail
        if len(view) > 0:
            sel = st.selectbox("View full CAP detail", view["CONTRACT_ID"].tolist(), key="cap_sel")
            rows = cap_df[cap_df["CONTRACT_ID"] == sel]
            for _, row in rows.iterrows():
                with st.expander(f"{row.get('PLAN_NAME',sel)} — {row.get('Issue_Type','')}", expanded=True):
                    st.markdown(f"**Contact:** {row.get('RECIPIENT_NAME','N/A')} | {row.get('EMAIL','N/A')}")
                    st.markdown(f"**Issue:** {row.get('Issue_Topic','N/A')}")
                    if "Issue_Summary" in row and pd.notna(row["Issue_Summary"]):
                        st.info(str(row["Issue_Summary"])[:1200])

        st.download_button("⬇️ Download", view.to_csv(index=False), "cap_actions.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — STAR RATINGS
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("⭐ Star Ratings")

    wh3 = where_clause()
    star_sql = f"""
    SELECT
        V.CONTRACT_ID,
        V.ORGANIZATION_MARKETING_NAME   AS PLAN_NAME,
        V.PARENT_ORGANIZATION,
        V.LEGAL_ENTITY_STATE_CODE       AS STATE,
        TRY_TO_NUMBER(V.MBR_CNT)       AS ENROLLMENT,
        V."2026_OVERALL"                AS OVERALL_STARS,
        V."2026_PART_C_SUMMARY"         AS PART_C_STARS,
        V."2026_PART_D_SUMMARY"         AS PART_D_STARS,
        V.REASON_FOR_LPI,
        V.OVERALL_FAC                   AS CAI_FLAG,
        V.OPPORTUNITY_SCORE,
        {CONSULTING_FIT_V}              AS CONSULTING_FIT
    FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
    {wh3}
    ORDER BY TRY_TO_DECIMAL(V."2026_OVERALL") ASC NULLS LAST
    LIMIT 500
    """

    if st.button("Load Star Ratings", type="primary", key="load_stars"):
        with st.spinner("Loading..."):
            try:
                st.session_state["star_df"] = run_query(star_sql)
            except Exception as e:
                st.error(f"{e}")

    if "star_df" in st.session_state:
        sdf = st.session_state["star_df"]
        num = pd.to_numeric(sdf["OVERALL_STARS"], errors="coerce")
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Plans", len(sdf))
        c2.metric("Below 3.0 ⭐", int((num < 3.0).sum()))
        c3.metric("3.0-3.4 ⭐", int(((num >= 3.0)&(num < 3.5)).sum()))
        c4.metric("4.0+ ⭐", int((num >= 4.0).sum()))

        t1, t2, t3 = st.tabs(["All", "Below 3.0", "4.0+"])
        with t1:
            st.dataframe(sdf, use_container_width=True, height=400)
        with t2:
            st.dataframe(sdf[pd.to_numeric(sdf["OVERALL_STARS"],errors="coerce") < 3.0],
                         use_container_width=True, height=400)
        with t3:
            st.dataframe(sdf[pd.to_numeric(sdf["OVERALL_STARS"],errors="coerce") >= 4.0],
                         use_container_width=True, height=400)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — AI ASSISTANT
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("🤖 AI Consulting Assistant")
    st.caption("Powered by Snowflake Cortex. Knows you run a small boutique firm — gives focused, actionable answers.")

    QUICK_SQL = {
        "Who should I reach out to first?": """
            SELECT V.CONTRACT_ID, V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   TRY_TO_NUMBER(V.MBR_CNT) AS ENROLLMENT,
                   V."2026_OVERALL" AS OVERALL_STARS, V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.Organization_Contact_Name) AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL,
                   COALESCE(C.Issue_Type, V.Issue_Type) AS CAP_ISSUE, V.REASON_FOR_LPI
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
                ON TRIM(V.CONTRACT_ID) = TRIM(C.CONTRACT_ID)
            WHERE TRY_TO_NUMBER(V.MBR_CNT) < 100000
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%HUMANA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%UNITED%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%AETNA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CVS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CENTENE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ANTHEM%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ELEVANCE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%BCBS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%BLUE CROSS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%KAISER%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CIGNA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%WELLCARE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%MOLINA%'
            ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST LIMIT 10""",

        "Plans with CAP issues and low stars": """
            SELECT V.CONTRACT_ID, V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   TRY_TO_NUMBER(V.MBR_CNT) AS ENROLLMENT,
                   V."2026_OVERALL" AS OVERALL_STARS, V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.Organization_Contact_Name) AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL,
                   COALESCE(C.Issue_Type, V.Issue_Type) AS CAP_ISSUE
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
                ON TRIM(V.CONTRACT_ID) = TRIM(C.CONTRACT_ID)
            WHERE V.Issue_Type IS NOT NULL
            AND TRY_TO_DECIMAL(V."2026_OVERALL") < 3.5
            AND TRY_TO_NUMBER(V.MBR_CNT) < 150000
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%HUMANA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%UNITED%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%AETNA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CVS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CENTENE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ANTHEM%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ELEVANCE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%BCBS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%KAISER%'
            ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST LIMIT 20""",

        "Small plans below 3 stars": """
            SELECT V.CONTRACT_ID, V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   TRY_TO_NUMBER(V.MBR_CNT) AS ENROLLMENT,
                   V."2026_OVERALL" AS OVERALL_STARS, V."2026_PART_C_SUMMARY" AS PART_C,
                   V."2026_PART_D_SUMMARY" AS PART_D, V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.Organization_Contact_Name) AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
                ON TRIM(V.CONTRACT_ID) = TRIM(C.CONTRACT_ID)
            WHERE TRY_TO_DECIMAL(V."2026_OVERALL") < 3.0
            AND TRY_TO_NUMBER(V.MBR_CNT) < 50000
            ORDER BY TRY_TO_DECIMAL(V."2026_OVERALL") ASC NULLS LAST LIMIT 25""",

        "Low performer plans": """
            SELECT V.CONTRACT_ID, V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   TRY_TO_NUMBER(V.MBR_CNT) AS ENROLLMENT,
                   V."2026_OVERALL" AS OVERALL_STARS, V.REASON_FOR_LPI, V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.Organization_Contact_Name) AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
                ON TRIM(V.CONTRACT_ID) = TRIM(C.CONTRACT_ID)
            WHERE V.REASON_FOR_LPI IS NOT NULL
            ORDER BY TRY_TO_NUMBER(V.MBR_CNT) ASC NULLS LAST LIMIT 25""",

        "CAP contacts for small independent plans": """
            SELECT C.CONTRACT_ID, C.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   C.Parent_Organization_Name AS PARENT_ORG,
                   C.LEGAL_ENTITY_STATE_CODE AS STATE, C.MBR_CNT AS ENROLLMENT,
                   C.RECIPIENT_NAME, C.EMAIL, C.Issue_Type, C.Issue_Topic,
                   V."2026_OVERALL" AS OVERALL_STARS, V.OPPORTUNITY_SCORE
            FROM MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
                ON TRIM(C.CONTRACT_ID) = TRIM(V.CONTRACT_ID)
            WHERE TRY_TO_NUMBER(C.MBR_CNT) < 100000
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%HUMANA%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%UNITED%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%AETNA%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%CVS%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%CENTENE%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%ANTHEM%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%ELEVANCE%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%BCBS%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%KAISER%'
            AND C.RECIPIENT_NAME IS NOT NULL
            ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST LIMIT 30""",
    }

    if "chat_msgs" not in st.session_state:
        st.session_state.chat_msgs = []
    if "chat_run" not in st.session_state:
        st.session_state.chat_run = False

    # Quick buttons
    st.subheader("Quick Questions")
    btn_cols = st.columns(3)
    for i, q in enumerate(QUICK_SQL.keys()):
        if btn_cols[i % 3].button(q, key=f"qb_{i}", use_container_width=True):
            last = st.session_state.chat_msgs[-1]["content"] if st.session_state.chat_msgs else None
            if not isinstance(last, str) or last != q:
                st.session_state.chat_msgs.append({"role": "user", "content": q})
                st.session_state.chat_run = True
                st.rerun()

    st.divider()
    user_q = st.chat_input("Ask anything — e.g. 'who should I call in Florida?' or 'what's my best pitch for UCare?'")
    if user_q:
        last = st.session_state.chat_msgs[-1]["content"] if st.session_state.chat_msgs else None
        if not isinstance(last, str) or last != user_q:
            st.session_state.chat_msgs.append({"role": "user", "content": user_q})
            st.session_state.chat_run = True

    # Display history
    for msg in st.session_state.chat_msgs:
        role = msg["role"] if msg["role"] in ["user","assistant"] else "assistant"
        with st.chat_message(role):
            if isinstance(msg["content"], pd.DataFrame):
                st.dataframe(msg["content"], use_container_width=True, height=300)
            else:
                st.markdown(str(msg["content"]))

    # Process
    if st.session_state.chat_run and st.session_state.chat_msgs:
        st.session_state.chat_run = False
        last_q = st.session_state.chat_msgs[-1]["content"]
        if isinstance(last_q, str):
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        cur = get_cursor()

                        if last_q in QUICK_SQL:
                            cur.execute(QUICK_SQL[last_q])
                            cols_ = [c[0] for c in cur.description]
                            rdf = pd.DataFrame(cur.fetchall(), columns=cols_)
                            st.success(f"✅ {len(rdf)} results from Snowflake")
                            st.dataframe(rdf, use_container_width=True, height=320)
                            st.session_state.chat_msgs.append({"role":"assistant","content":rdf})

                            if len(rdf) > 0:
                                try:
                                    preview = rdf.head(5).to_string(index=False)
                                    p = f"{SYSTEM_CONTEXT}\n\nQuestion: {last_q}\n\nData:\n{preview}\n\nGive TOP 3 recommendations with contact details and one-line pitch for each. Be specific and actionable."
                                    p_esc = p.replace("\\","\\\\").replace("'","\\'")
                                    cur2 = get_cursor()
                                    cur2.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{p_esc}') AS A")
                                    raw = cur2.fetchone()[0]
                                    try:
                                        ans = json.loads(raw)["choices"][0]["message"]["content"]
                                    except Exception:
                                        ans = raw
                                    st.markdown("**💡 Action Plan:**")
                                    st.markdown(ans)
                                    st.session_state.chat_msgs.append({"role":"assistant","content":"**💡 Action Plan:**\n"+ans})
                                except Exception:
                                    pass
                        else:
                            # AI generates SQL
                            sp = (f"{SYSTEM_CONTEXT}\n\nGenerate Snowflake SQL for: {last_q}\n\n"
                                  f"Tables:\n"
                                  f"1. MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB (alias V): "
                                  f"CONTRACT_ID, ORGANIZATION_MARKETING_NAME, PARENT_ORGANIZATION, MBR_CNT, "
                                  f"LEGAL_ENTITY_STATE_CODE, PLAN_TYPE, \"2026_OVERALL\", \"2026_PART_C_SUMMARY\", "
                                  f"\"2026_PART_D_SUMMARY\", REASON_FOR_LPI, OVERALL_FAC, Issue_Type, Issue_Summary, "
                                  f"Organization_Contact_Name, Organization_Contact_Phone, DIRECTORY_CONTACT_EMAIL, OPPORTUNITY_SCORE\n"
                                  f"2. MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY (alias C): "
                                  f"CONTRACT_ID, ORGANIZATION_MARKETING_NAME, Parent_Organization_Name, "
                                  f"RECIPIENT_NAME, EMAIL, Issue_Type, Issue_Topic, Issue_Summary, LEGAL_ENTITY_STATE_CODE, MBR_CNT\n"
                                  f"JOIN ON: TRIM(V.CONTRACT_ID) = TRIM(C.CONTRACT_ID)\n"
                                  f"NOTE: Quote columns with special chars: \"2026_OVERALL\" etc.\n"
                                  f"Return ONLY SQL, no markdown, no backticks. LIMIT 25.")
                            sp_esc = sp.replace("\\","\\\\").replace("'","\\'")
                            cur.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{sp_esc}') AS S")
                            raw_s = cur.fetchone()[0]
                            try:
                                gen = json.loads(raw_s)["choices"][0]["message"]["content"].strip()
                            except Exception:
                                gen = raw_s.strip()
                            gen = gen.replace("```sql","").replace("```","").strip()

                            try:
                                cur.execute(gen)
                                cols_ = [c[0] for c in cur.description]
                                rdf = pd.DataFrame(cur.fetchall(), columns=cols_)
                                st.success(f"✅ {len(rdf)} results")
                                st.dataframe(rdf, use_container_width=True, height=320)
                                st.session_state.chat_msgs.append({"role":"assistant","content":rdf})
                                if len(rdf) > 0:
                                    try:
                                        preview = rdf.head(5).to_string(index=False)
                                        ap = f"{SYSTEM_CONTEXT}\n\nQ: {last_q}\n\nData:\n{preview}\n\nGive TOP 3 targets with contact + pitch."
                                        ap_esc = ap.replace("\\","\\\\").replace("'","\\'")
                                        cur3 = get_cursor()
                                        cur3.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{ap_esc}') AS A")
                                        raw_a = cur3.fetchone()[0]
                                        try:
                                            ans = json.loads(raw_a)["choices"][0]["message"]["content"]
                                        except Exception:
                                            ans = raw_a
                                        st.markdown("**💡 Action Plan:**")
                                        st.markdown(ans)
                                        st.session_state.chat_msgs.append({"role":"assistant","content":ans})
                                    except Exception:
                                        pass
                            except Exception:
                                # Fallback
                                fb = f"{SYSTEM_CONTEXT}\n\nAnswer based on MA knowledge: {last_q}\nBe specific, give top 3 targets."
                                fb_esc = fb.replace("\\","\\\\").replace("'","\\'")
                                cur4 = get_cursor()
                                cur4.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{fb_esc}') AS A")
                                raw_fb = cur4.fetchone()[0]
                                try:
                                    fb_ans = json.loads(raw_fb)["choices"][0]["message"]["content"]
                                except Exception:
                                    fb_ans = raw_fb
                                st.markdown(fb_ans)
                                st.session_state.chat_msgs.append({"role":"assistant","content":fb_ans})

                    except Exception as e:
                        st.error(f"Error: {e}")

    if st.session_state.chat_msgs:
        if st.button("🗑️ Clear conversation", key="clear_chat"):
            st.session_state.chat_msgs = []
            st.rerun()
