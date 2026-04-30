import streamlit as st
import pandas as pd
import snowflake.connector
import json

st.set_page_config(page_title="MA Intelligence Hub", page_icon="🎯", layout="wide")

# ─── CONNECTION ───────────────────────────────────────────────────────────────
@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=st.secrets["snowflake"]["account"],
        user=st.secrets["snowflake"]["user"],
        password=st.secrets["snowflake"]["password"],
        warehouse=st.secrets["snowflake"]["warehouse"],
        database=st.secrets["snowflake"]["database"],
        schema=st.secrets["snowflake"]["schema"],
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

# ─── TABLE / COLUMN REFERENCE (from DESCRIBE TABLE output) ───────────────────
#
# VW_MA_INTELLIGENCE_HUB  (alias V)
#   CONTRACT_ID, ORGANIZATION_MARKETING_NAME, PARENT_ORGANIZATION
#   RECIPIENT_NAME, RECIPIENT_EMAIL, DATE_OF_LETTER_LATEST
#   MBR_CNT, LEGAL_ENTITY_NAME, LEGAL_ENTITY_STATE_CODE, PLAN_TYPE
#   DIRECTORY_CONTACT_FIRST_NAME, DIRECTORY_CONTACT_LAST_NAME,
#   DIRECTORY_CONTACT_PHONE, DIRECTORY_CONTACT_EMAIL
#   "2026_PART_C_SUMMARY", "2026_PART_D_SUMMARY", "2026_OVERALL"
#   REASON_FOR_LPI, OVERALL_FAC, PART_C_FAC, PART_D_MAPD_FAC
#   Issue_Type, Issue_Summary, Organization_Contact_Name, Organization_Contact_Phone
#   OPPORTUNITY_SCORE
#   C01..C33 star scores, D01..D12 star scores (+ _DATA variants)
#
# CONTRACTS_CAP_SUMMARY  (alias C)
#   Contract_ID  ← NOTE: mixed case, not CONTRACT_ID
#   RECIPIENT_NAME, EMAIL, SUMMARY, FILE_NAME, DATE_OF_LETTER
#   Parent_Organization_Name, Issue_Type, Issue_Topic, Issue_Summary
#   ORGANIZATION_MARKETING_NAME, ORGANIZATION_TYPE
#   LEGAL_ENTITY_NAME, LEGAL_ENTITY_CITY, LEGAL_ENTITY_STATE_CODE
#   PLAN_TYPE, CONTRACT_EFFECTIVE_DATE, TAX_STATUS, MBR_CNT
#
# JOIN: TRIM(V.CONTRACT_ID) = TRIM(C."Contract_ID")

V  = "MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB"
C  = "MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY"

# Large national filter on VIEW table
LN = [
    "HUMANA","UNITED","UNITEDHEALTHCARE","AETNA","CVS","CENTENE","MOLINA",
    "ANTHEM","ELEVANCE","BCBS","BLUE CROSS","BLUE SHIELD",
    "HEALTH CARE SERVICE","HCSC","KAISER","CIGNA","WELLCARE",
    "DEVOTED","OSCAR","BRIGHT HEALTH"
]
LN_WHERE_V = " AND ".join([f"UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%{n}%'" for n in LN])
LN_WHERE_C = " AND ".join([f'UPPER(C.Parent_Organization_Name) NOT LIKE \'%{n}%\'' for n in LN])

# Consulting fit label
FIT_CASE = """CASE
    WHEN UPPER(V.PARENT_ORGANIZATION) LIKE '%HUMANA%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%UNITED%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%AETNA%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CVS%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CENTENE%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%MOLINA%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ANTHEM%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ELEVANCE%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BCBS%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BLUE CROSS%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%KAISER%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CIGNA%'
      OR UPPER(V.PARENT_ORGANIZATION) LIKE '%WELLCARE%'
      THEN 'Large National'
    WHEN V.MBR_CNT > 150000 THEN 'Large - Has Team'
    WHEN V.MBR_CNT > 50000  THEN 'Mid-Size - Maybe'
    ELSE 'Small/Regional - TARGET'
END"""

AI_CONTEXT = """You are an expert Medicare Advantage analyst for Sadaf, who runs a SMALL BOUTIQUE consulting firm.

FIRM PROFILE:
- Solo/small — can realistically serve 2-5 clients at a time
- Target: small/regional MA plans (<50K members) with NO in-house analytics team
- Services: star ratings improvement, risk adjustment analytics, compliance remediation
- SKIP large national plans (Humana, United, Aetna, CVS, Centene, Molina, Anthem/Elevance, BCBS, Kaiser, Cigna, WellCare)

CONTACT PRIORITY: CAP letter recipient name/email first → View RECIPIENT_NAME/RECIPIENT_EMAIL → Directory contact

When asked about outreach:
1. Recommend TOP 3 plans only — small firm, focus matters
2. For each: Plan, Contract ID, State, Enrollment, Stars, Contact Name, Email, why they need help
3. End with a one-line cold outreach email subject for each
4. Be direct and specific — Sadaf has deep MA analytics expertise"""

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🎯 MA Intelligence Hub")
    st.caption("Sadaf's consulting opportunity engine")
    st.divider()
    st.subheader("🔍 Filters")
    target_only    = st.checkbox("Targets only (hide large nationals)", value=True)
    max_enroll     = st.number_input("Max Enrollment", 0, 1000000, 150000, 10000)
    cap_only       = st.checkbox("CAP Plans Only")
    lpi_only       = st.checkbox("Low Performers Only")
    states         = st.multiselect("State", ["All","AL","AK","AZ","AR","CA","CO","CT","DE","FL",
                                               "GA","HI","ID","IL","IN","IA","KS","KY","LA","ME",
                                               "MD","MA","MI","MN","MS","MO","MT","NE","NV","NH",
                                               "NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI",
                                               "SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"],
                                    default=["All"])

def build_where():
    w = []
    if target_only:
        w.append(LN_WHERE_V)
    if max_enroll > 0:
        w.append(f"V.MBR_CNT <= {max_enroll}")
    if cap_only:
        w.append("V.Issue_Type IS NOT NULL")
    if lpi_only:
        w.append("V.REASON_FOR_LPI IS NOT NULL")
    if states and "All" not in states:
        s = ",".join([f"'{x}'" for x in states])
        w.append(f"V.LEGAL_ENTITY_STATE_CODE IN ({s})")
    return ("WHERE " + " AND ".join(w)) if w else ""

# ─── TABS ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "🎯 Opportunities", "📋 CAP Actions", "⭐ Star Ratings", "🤖 AI Assistant"
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OPPORTUNITIES
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("🎯 Consulting Opportunities")
    st.caption("Ranked by opportunity score. CAP recipient shown first, then view contact, then directory.")

    wh = build_where()

    OPP_SQL = """
    SELECT
        V.CONTRACT_ID,
        V.ORGANIZATION_MARKETING_NAME                                           AS PLAN_NAME,
        V.PARENT_ORGANIZATION,
        V.LEGAL_ENTITY_STATE_CODE                                               AS STATE,
        V.PLAN_TYPE,
        V.MBR_CNT                                                               AS ENROLLMENT,
        V."2026_OVERALL"                                                        AS OVERALL_STARS,
        V."2026_PART_C_SUMMARY"                                                 AS PART_C_STARS,
        V."2026_PART_D_SUMMARY"                                                 AS PART_D_STARS,
        V.OPPORTUNITY_SCORE,
        COALESCE(C.RECIPIENT_NAME,
                 V.RECIPIENT_NAME,
                 V.DIRECTORY_CONTACT_FIRST_NAME || ' ' || V.DIRECTORY_CONTACT_LAST_NAME)
                                                                                AS CONTACT_NAME,
        COALESCE(C.EMAIL,
                 V.RECIPIENT_EMAIL,
                 V.DIRECTORY_CONTACT_EMAIL)                                     AS CONTACT_EMAIL,
        V.DIRECTORY_CONTACT_PHONE                                               AS CONTACT_PHONE,
        C.DATE_OF_LETTER                                                        AS CAP_LETTER_DATE,
        COALESCE(C."Issue_Type",  V."Issue_Type")                               AS CAP_ISSUE_TYPE,
        COALESCE(C."Issue_Topic", '')                                          AS CAP_ISSUE_TOPIC,
        COALESCE(C."Issue_Summary", V."Issue_Summary")                          AS CAP_ISSUE_SUMMARY,
        V.REASON_FOR_LPI,
        V.OVERALL_FAC                                                           AS CAI_FLAG,
        CASE
            WHEN UPPER(V.PARENT_ORGANIZATION) LIKE '%HUMANA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%UNITED%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%AETNA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CVS%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CENTENE%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%MOLINA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ANTHEM%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ELEVANCE%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BCBS%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BLUE CROSS%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%KAISER%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CIGNA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%WELLCARE%'
              THEN 'Large National'
            WHEN V.MBR_CNT > 150000 THEN 'Large - Has Team'
            WHEN V.MBR_CNT > 50000  THEN 'Mid-Size - Maybe'
            ELSE 'Small/Regional - TARGET'
        END                                                                     AS CONSULTING_FIT
    FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
    LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
        ON TRIM(V.CONTRACT_ID) = TRIM(C."Contract_ID")
    """ + build_where() + """
    ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST, V.MBR_CNT ASC NULLS LAST
    LIMIT 300
    """

    c1, c2, c3 = st.columns([2,1,1])
    min_score = c1.slider("Min Opportunity Score", 0, 70, 0, 5)
    show_n    = c2.selectbox("Show top", [25, 50, 100, 200], 0)
    run_opp   = c3.button("🔍 Find Opportunities", type="primary", use_container_width=True)

    if run_opp or "opp_df" not in st.session_state:
        with st.spinner("Querying Snowflake..."):
            try:
                df = run_query(OPP_SQL)
                if min_score > 0:
                    df = df[pd.to_numeric(df["OPPORTUNITY_SCORE"], errors="coerce") >= min_score]
                st.session_state["opp_df"] = df.head(show_n)
            except Exception as e:
                st.error(f"Error: {e}")

    if "opp_df" in st.session_state:
        df = st.session_state["opp_df"]
        targets = df[df["CONSULTING_FIT"] == "Small/Regional - TARGET"]
        maybe   = df[df["CONSULTING_FIT"] == "Mid-Size - Maybe"]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Plans Found", len(df))
        m2.metric("✅ Prime Targets", len(targets))
        m3.metric("🟡 Mid-Size", len(maybe))
        scores = pd.to_numeric(df["OPPORTUNITY_SCORE"], errors="coerce")
        m4.metric("Avg Opp Score", f"{scores.mean():.0f}" if scores.notna().any() else "N/A")

        display = ["CONTRACT_ID","PLAN_NAME","STATE","ENROLLMENT","OVERALL_STARS",
                   "OPPORTUNITY_SCORE","CONSULTING_FIT","CONTACT_NAME","CONTACT_EMAIL",
                   "CAP_ISSUE_TYPE","REASON_FOR_LPI","CAI_FLAG"]
        st.dataframe(df[[c for c in display if c in df.columns]],
                     use_container_width=True, height=420)

        # Plan detail card
        st.subheader("📋 Plan Detail")
        sel = st.selectbox("Select a plan", df["PLAN_NAME"].tolist(), key="opp_sel")
        if sel:
            row = df[df["PLAN_NAME"] == sel].iloc[0]
            fit = row.get("CONSULTING_FIT", "")
            bg  = "#e8f5e9" if "TARGET" in fit else "#fff9c4" if "Maybe" in fit else "#ffebee"
            st.markdown(
                f'<div style="background:{bg};padding:14px;border-radius:8px">'
                f'<b>{row.get("PLAN_NAME","")} ({row.get("CONTRACT_ID","")})</b> — '
                f'{row.get("PARENT_ORGANIZATION","")} | {row.get("STATE","")} | '
                f'Enrollment: {row.get("ENROLLMENT","")} | <b>{fit}</b></div>',
                unsafe_allow_html=True
            )
            r1, r2, r3 = st.columns(3)
            with r1:
                st.markdown("**⭐ Stars**")
                st.write(f"Overall: **{row.get('OVERALL_STARS','N/A')}**")
                st.write(f"Part C: {row.get('PART_C_STARS','N/A')}")
                st.write(f"Part D: {row.get('PART_D_STARS','N/A')}")
                st.write(f"Opp Score: **{row.get('OPPORTUNITY_SCORE','N/A')}**")
            with r2:
                st.markdown("**⚠️ Compliance**")
                st.write(f"CAP Issue: {row.get('CAP_ISSUE_TYPE','None')}")
                st.write(f"CAP Topic: {row.get('CAP_ISSUE_TOPIC','None')}")
                st.write(f"CAP Date: {row.get('CAP_LETTER_DATE','N/A')}")
                st.write(f"Low Performer: {row.get('REASON_FOR_LPI','No') or 'No'}")
                st.write(f"CAI Flag: {row.get('CAI_FLAG','No') or 'No'}")
            with r3:
                st.markdown("**📞 Contact**")
                st.write(f"Name: **{row.get('CONTACT_NAME','N/A')}**")
                st.write(f"Email: {row.get('CONTACT_EMAIL','N/A')}")
                st.write(f"Phone: {row.get('CONTACT_PHONE','N/A')}")
            if row.get("CAP_ISSUE_SUMMARY"):
                st.markdown("**CAP Summary:**")
                st.info(str(row["CAP_ISSUE_SUMMARY"])[:1200])

        st.download_button("⬇️ Download CSV", df.to_csv(index=False), "opportunities.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CAP ACTIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("📋 CAP Enforcement Actions")
    st.caption("CMS corrective actions — compliance contacts are warm leads for consulting outreach")

    CAP_SQL = """
    SELECT
        C."Contract_ID"                             AS CONTRACT_ID,
        C.ORGANIZATION_MARKETING_NAME               AS PLAN_NAME,
        C.Parent_Organization_Name                  AS PARENT_ORG,
        C.LEGAL_ENTITY_STATE_CODE                   AS STATE,
        C.PLAN_TYPE,
        C.MBR_CNT                                   AS ENROLLMENT,
        C.RECIPIENT_NAME,
        C.EMAIL,
        C.DATE_OF_LETTER,
        C.Issue_Type,
        C.Issue_Topic,
        C.Issue_Summary,
        V."2026_OVERALL"                            AS OVERALL_STARS,
        V.OPPORTUNITY_SCORE,
        CASE
            WHEN UPPER(C.Parent_Organization_Name) LIKE '%HUMANA%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%UNITED%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%AETNA%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%CVS%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%CENTENE%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%MOLINA%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%ANTHEM%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%ELEVANCE%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%BCBS%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%BLUE CROSS%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%KAISER%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%CIGNA%'
              OR UPPER(C.Parent_Organization_Name) LIKE '%WELLCARE%'
              THEN 'Large National'
            WHEN C.MBR_CNT > 150000 THEN 'Large - Has Team'
            WHEN C.MBR_CNT > 50000  THEN 'Mid-Size - Maybe'
            ELSE 'Small/Regional - TARGET'
        END                                         AS CONSULTING_FIT
    FROM MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
    LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
        ON TRIM(C."Contract_ID") = TRIM(V.CONTRACT_ID)
    ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST
    LIMIT 500
    """

    if st.button("Load CAP Data", type="primary", key="load_cap"):
        with st.spinner("Loading..."):
            try:
                st.session_state["cap_df"] = run_query(CAP_SQL)
            except Exception as e:
                st.error(f"{e}")

    if "cap_df" in st.session_state:
        cdf = st.session_state["cap_df"]

        f1, f2 = st.columns(2)
        it_opts = ["All"] + sorted(cdf["ISSUE_TYPE"].dropna().unique().tolist()) if "ISSUE_TYPE" in cdf.columns else ["All"]
        sel_it  = f1.selectbox("Filter Issue Type", it_opts)
        show_ln = f2.checkbox("Show large nationals", value=False)

        view = cdf.copy()
        if sel_it != "All" and "ISSUE_TYPE" in view.columns:
            view = view[view["ISSUE_TYPE"] == sel_it]
        if not show_ln:
            for n in LN:
                view = view[~view["PARENT_ORG"].str.upper().str.contains(n, na=False)]

        st.metric("CAP Actions", len(view))
        show_c = [c for c in ["CONTRACT_ID","PLAN_NAME","STATE","ENROLLMENT","ISSUE_TYPE",
                               "RECIPIENT_NAME","EMAIL","DATE_OF_LETTER",
                               "OVERALL_STARS","CONSULTING_FIT"] if c in view.columns]
        # Try lowercase too
        show_c2 = [c for c in ["CONTRACT_ID","PLAN_NAME","STATE","ENROLLMENT","Issue_Type",
                                "RECIPIENT_NAME","EMAIL","DATE_OF_LETTER",
                                "OVERALL_STARS","CONSULTING_FIT"] if c in view.columns]
        st.dataframe(view[show_c2], use_container_width=True, height=400)

        if len(view) > 0:
            sel_cap = st.selectbox("View full CAP detail", view["CONTRACT_ID"].tolist(), key="cap_sel")
            rows = cdf[cdf["CONTRACT_ID"] == sel_cap]
            for _, row in rows.iterrows():
                with st.expander(f"{row.get('PLAN_NAME', sel_cap)} — {row.get('Issue_Type','')}", expanded=True):
                    st.markdown(f"**Contact:** {row.get('RECIPIENT_NAME','N/A')} | {row.get('EMAIL','N/A')}")
                    st.markdown(f"**Date:** {row.get('DATE_OF_LETTER','N/A')}")
                    st.markdown(f"**Issue Topic:** {row.get('Issue_Topic','N/A')}")
                    summ = row.get("Issue_Summary","")
                    if pd.notna(summ) and summ:
                        st.info(str(summ)[:1500])

        st.download_button("⬇️ Download", view.to_csv(index=False), "cap_actions.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — STAR RATINGS
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("⭐ Star Ratings")

    wh3 = build_where()
    STAR_SQL = f"""
    SELECT
        V.CONTRACT_ID,
        V.ORGANIZATION_MARKETING_NAME   AS PLAN_NAME,
        V.PARENT_ORGANIZATION,
        V.LEGAL_ENTITY_STATE_CODE       AS STATE,
        V.MBR_CNT                       AS ENROLLMENT,
        V."2026_OVERALL"                AS OVERALL_STARS,
        V."2026_PART_C_SUMMARY"         AS PART_C_STARS,
        V."2026_PART_D_SUMMARY"         AS PART_D_STARS,
        V.REASON_FOR_LPI,
        V.OVERALL_FAC                   AS CAI_FLAG,
        V.OPPORTUNITY_SCORE,
        CASE
            WHEN UPPER(V.PARENT_ORGANIZATION) LIKE '%HUMANA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%UNITED%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%AETNA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CVS%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CENTENE%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%MOLINA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ANTHEM%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%ELEVANCE%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BCBS%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%BLUE CROSS%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%KAISER%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%CIGNA%'
              OR UPPER(V.PARENT_ORGANIZATION) LIKE '%WELLCARE%'
              THEN 'Large National'
            WHEN V.MBR_CNT > 150000 THEN 'Large - Has Team'
            WHEN V.MBR_CNT > 50000  THEN 'Mid-Size - Maybe'
            ELSE 'Small/Regional - TARGET'
        END                             AS CONSULTING_FIT
    FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
    {wh3}
    ORDER BY TRY_TO_DECIMAL(V."2026_OVERALL") ASC NULLS LAST
    LIMIT 500
    """

    if st.button("Load Star Ratings", type="primary", key="load_stars"):
        with st.spinner("Loading..."):
            try:
                st.session_state["star_df"] = run_query(STAR_SQL)
            except Exception as e:
                st.error(f"{e}")

    if "star_df" in st.session_state:
        sdf = st.session_state["star_df"]
        num = pd.to_numeric(sdf["OVERALL_STARS"], errors="coerce")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Plans", len(sdf))
        c2.metric("Below 3.0 ⭐", int((num < 3.0).sum()))
        c3.metric("3.0–3.4 ⭐", int(((num >= 3.0)&(num < 3.5)).sum()))
        c4.metric("4.0+ ⭐", int((num >= 4.0).sum()))

        t1, t2, t3 = st.tabs(["All Plans", "Below 3.0", "4.0+"])
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
    st.caption("Powered by Snowflake Cortex + live data. Knows you run a small boutique firm.")

    # Pre-built queries with EXACT column names
    QUICK_SQL = {
        "Who should I reach out to first?": f"""
            SELECT V.CONTRACT_ID,
                   V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   V.MBR_CNT AS ENROLLMENT, V."2026_OVERALL" AS OVERALL_STARS,
                   V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.RECIPIENT_NAME,
                            V.DIRECTORY_CONTACT_FIRST_NAME || ' ' || V.DIRECTORY_CONTACT_LAST_NAME)
                            AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.RECIPIENT_EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL,
                   COALESCE(C.Issue_Type, V.Issue_Type) AS CAP_ISSUE,
                   V.REASON_FOR_LPI
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C ON TRIM(V.CONTRACT_ID) = TRIM(C."Contract_ID")
            WHERE V.MBR_CNT < 100000
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%HUMANA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%UNITED%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%AETNA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CVS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CENTENE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%MOLINA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ANTHEM%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ELEVANCE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%BCBS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%BLUE CROSS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%KAISER%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CIGNA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%WELLCARE%'
            ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST LIMIT 10""",

        "Plans with CAP issues + low stars": f"""
            SELECT V.CONTRACT_ID, V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   V.MBR_CNT AS ENROLLMENT, V."2026_OVERALL" AS OVERALL_STARS,
                   V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.RECIPIENT_NAME) AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.RECIPIENT_EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL,
                   COALESCE(C.Issue_Type, V.Issue_Type) AS CAP_ISSUE,
                   C.DATE_OF_LETTER AS CAP_DATE
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C ON TRIM(V.CONTRACT_ID) = TRIM(C."Contract_ID")
            WHERE V.Issue_Type IS NOT NULL
            AND TRY_TO_DECIMAL(V."2026_OVERALL") < 3.5
            AND V.MBR_CNT < 150000
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%HUMANA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%UNITED%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%AETNA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CVS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CENTENE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%MOLINA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ANTHEM%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%ELEVANCE%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%BCBS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%BLUE CROSS%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%KAISER%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%CIGNA%'
            AND UPPER(V.PARENT_ORGANIZATION) NOT LIKE '%WELLCARE%'
            ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST LIMIT 20""",

        "Small plans below 3 stars": f"""
            SELECT V.CONTRACT_ID, V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   V.MBR_CNT AS ENROLLMENT,
                   V."2026_OVERALL" AS OVERALL_STARS,
                   V."2026_PART_C_SUMMARY" AS PART_C, V."2026_PART_D_SUMMARY" AS PART_D,
                   V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.RECIPIENT_NAME) AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.RECIPIENT_EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C ON TRIM(V.CONTRACT_ID) = TRIM(C."Contract_ID")
            WHERE TRY_TO_DECIMAL(V."2026_OVERALL") < 3.0
            AND V.MBR_CNT < 50000
            ORDER BY TRY_TO_DECIMAL(V."2026_OVERALL") ASC NULLS LAST LIMIT 25""",

        "Low performer plans": f"""
            SELECT V.CONTRACT_ID, V.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   V.PARENT_ORGANIZATION, V.LEGAL_ENTITY_STATE_CODE AS STATE,
                   V.MBR_CNT AS ENROLLMENT, V."2026_OVERALL" AS OVERALL_STARS,
                   V.REASON_FOR_LPI, V.OPPORTUNITY_SCORE,
                   COALESCE(C.RECIPIENT_NAME, V.RECIPIENT_NAME) AS CONTACT_NAME,
                   COALESCE(C.EMAIL, V.RECIPIENT_EMAIL, V.DIRECTORY_CONTACT_EMAIL) AS CONTACT_EMAIL
            FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C ON TRIM(V.CONTRACT_ID) = TRIM(C."Contract_ID")
            WHERE V.REASON_FOR_LPI IS NOT NULL
            ORDER BY V.MBR_CNT ASC NULLS LAST LIMIT 25""",

        "CAP contacts — small independent plans": f"""
            SELECT C."Contract_ID" AS CONTRACT_ID,
                   C.ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                   C.Parent_Organization_Name AS PARENT_ORG,
                   C.LEGAL_ENTITY_STATE_CODE AS STATE, C.MBR_CNT AS ENROLLMENT,
                   C.RECIPIENT_NAME, C.EMAIL, C.DATE_OF_LETTER,
                   C.Issue_Type, C.Issue_Topic,
                   V."2026_OVERALL" AS OVERALL_STARS, V.OPPORTUNITY_SCORE
            FROM MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY C
            LEFT JOIN MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB V ON TRIM(C."Contract_ID") = TRIM(V.CONTRACT_ID)
            WHERE C.MBR_CNT < 100000
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%HUMANA%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%UNITED%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%AETNA%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%CVS%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%CENTENE%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%MOLINA%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%ANTHEM%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%ELEVANCE%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%BCBS%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%BLUE CROSS%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%KAISER%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%CIGNA%'
            AND UPPER(C.Parent_Organization_Name) NOT LIKE '%WELLCARE%'
            AND C.RECIPIENT_NAME IS NOT NULL
            ORDER BY V.OPPORTUNITY_SCORE DESC NULLS LAST LIMIT 30""",
    }

    if "chat_msgs" not in st.session_state:
        st.session_state.chat_msgs = []
    if "chat_run" not in st.session_state:
        st.session_state.chat_run = False

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
    user_q = st.chat_input("Ask anything — e.g. 'who should I call in Florida?' or 'what's ATRIO's star rating?'")
    if user_q:
        last = st.session_state.chat_msgs[-1]["content"] if st.session_state.chat_msgs else None
        if not isinstance(last, str) or last != user_q:
            st.session_state.chat_msgs.append({"role": "user", "content": user_q})
            st.session_state.chat_run = True

    for msg in st.session_state.chat_msgs:
        role = "user" if msg["role"] == "user" else "assistant"
        with st.chat_message(role):
            if isinstance(msg["content"], pd.DataFrame):
                st.dataframe(msg["content"], use_container_width=True, height=300)
            else:
                st.markdown(str(msg["content"]))

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
                            rdf   = pd.DataFrame(cur.fetchall(), columns=cols_)
                            st.success(f"✅ {len(rdf)} results from Snowflake")
                            st.dataframe(rdf, use_container_width=True, height=320)
                            st.session_state.chat_msgs.append({"role":"assistant","content":rdf})

                            if len(rdf) > 0:
                                try:
                                    preview = rdf.head(5).to_string(index=False)
                                    p = (f"{AI_CONTEXT}\n\nQuestion: {last_q}\n\n"
                                         f"Live Snowflake data (top 5):\n{preview}\n\n"
                                         f"Give TOP 3 recommendations with contact details and one-line pitch per plan.")
                                    p_esc = p.replace("\\","\\\\").replace("'","\\'")
                                    cur2  = get_cursor()
                                    cur2.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{p_esc}') AS A")
                                    raw   = cur2.fetchone()[0]
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
                            sql_p = (
                                f"{AI_CONTEXT}\n\n"
                                f"Generate Snowflake SQL for: {last_q}\n\n"
                                f"TABLES (use exact column names):\n"
                                f"1. MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB AS V\n"
                                f"   Columns: CONTRACT_ID, ORGANIZATION_MARKETING_NAME, PARENT_ORGANIZATION, "
                                f"MBR_CNT, LEGAL_ENTITY_STATE_CODE, PLAN_TYPE, "
                                f"RECIPIENT_NAME, RECIPIENT_EMAIL, DATE_OF_LETTER_LATEST, "
                                f"DIRECTORY_CONTACT_FIRST_NAME, DIRECTORY_CONTACT_LAST_NAME, "
                                f"DIRECTORY_CONTACT_PHONE, DIRECTORY_CONTACT_EMAIL, "
                                f"\"2026_OVERALL\", \"2026_PART_C_SUMMARY\", \"2026_PART_D_SUMMARY\", "
                                f"REASON_FOR_LPI, OVERALL_FAC, PART_C_FAC, PART_D_MAPD_FAC, "
                                f"Issue_Type, Issue_Summary, Organization_Contact_Name, "
                                f"Organization_Contact_Phone, OPPORTUNITY_SCORE\n"
                                f"2. MA_ANALYTICS.DATA_PROCESSING.CONTRACTS_CAP_SUMMARY AS C\n"
                                f"   Columns: \"Contract_ID\" (MIXED CASE - always quote!), "
                                f"ORGANIZATION_MARKETING_NAME, Parent_Organization_Name, "
                                f"RECIPIENT_NAME, EMAIL, DATE_OF_LETTER, SUMMARY, FILE_NAME, "
                                f"Issue_Type, Issue_Topic, Issue_Summary, "
                                f"LEGAL_ENTITY_STATE_CODE, PLAN_TYPE, MBR_CNT\n\n"
                                f"JOIN: TRIM(V.CONTRACT_ID) = TRIM(C.\"Contract_ID\")\n"
                                f"CRITICAL: \"2026_OVERALL\", \"2026_PART_C_SUMMARY\", \"2026_PART_D_SUMMARY\" must be double-quoted\n"
                                f"CRITICAL: C.\"Contract_ID\" must be double-quoted (mixed case)\n"
                                f"Return ONLY valid Snowflake SQL. No markdown. No backticks. LIMIT 25."
                            )
                            sp_esc = sql_p.replace("\\","\\\\").replace("'","\\'")
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
                                rdf   = pd.DataFrame(cur.fetchall(), columns=cols_)
                                st.success(f"✅ {len(rdf)} results")
                                st.dataframe(rdf, use_container_width=True, height=320)
                                st.session_state.chat_msgs.append({"role":"assistant","content":rdf})

                                if len(rdf) > 0:
                                    try:
                                        preview = rdf.head(5).to_string(index=False)
                                        ap = (f"{AI_CONTEXT}\n\nQ: {last_q}\n\nData:\n{preview}\n\n"
                                              f"Give TOP 3 targets with contact + one-line pitch each.")
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
                                # Fallback: general knowledge
                                fb = (f"{AI_CONTEXT}\n\nAnswer from MA expertise: {last_q}\n"
                                      f"Give TOP 3 specific targets with rationale.")
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
