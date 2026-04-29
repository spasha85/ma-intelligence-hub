import streamlit as st
import pandas as pd
import snowflake.connector
import json

st.set_page_config(page_title="MA Intelligence Hub", page_icon="★", layout="wide")

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size:1.8rem; color:#1F4E79; font-weight:600; }
[data-testid="stMetricLabel"] { font-size:0.72rem; color:#666; }
.section-header { font-size:1.05rem; font-weight:600; color:#1F4E79;
    border-bottom:2px solid #1F4E79; padding-bottom:4px; margin:0.8rem 0 0.5rem; }
.filter-box { background:#F0F4F8; border-radius:8px; padding:12px; margin-bottom:12px; }
</style>""", unsafe_allow_html=True)

st.title("★ MA Intelligence Hub")
st.caption("Star Ratings · CAP Enforcement · Low Performers · Measure Performance · Enrollment · Contract Directory")
st.markdown("<div style='text-align:right; font-size:11px; color:#999; margin-top:-10px;'>Built by <b>Sadaf Pasha</b></div>", unsafe_allow_html=True)

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
        role      = st.secrets["snowflake"].get("role", ""),
    )

@st.cache_data(ttl=3600)
def run_query(sql):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=86400)
def load_measure_weights():
    """Load 2027 measure weights from Snowflake tables."""
    try:
        c_df = run_query(f"""
            SELECT DISTINCT MEASURE_NAME,
                   TRY_TO_NUMBER(PART_C_SUMMARY_AND_MA_PD_OVERALL_WEIGHT) AS WEIGHT
            FROM MA_ANALYTICS.DATA_PROCESSING.STAR_RATINGS_2027_PART_C_MEASURES
        """)
        d_df = run_query(f"""
            SELECT DISTINCT MEASURE_NAME,
                   TRY_TO_NUMBER(PART_D_SUMMARY_AND_MA_PD_OVERALL_WEIGHT) AS WEIGHT
            FROM MA_ANALYTICS.DATA_PROCESSING.STAR_RATINGS_2027_PART_D_MEASURES
        """)
        weights = {}
        for _, row in c_df.iterrows():
            weights[str(row["MEASURE_NAME"])] = row["WEIGHT"]
        for _, row in d_df.iterrows():
            weights[str(row["MEASURE_NAME"])] = row["WEIGHT"]
        return weights
    except Exception:
        return {}

DB = "MA_ANALYTICS.DATA_PROCESSING"

# ── BASE CTE ──────────────────────────────────────────────────────────────────
BASE_CTE = "WITH BASE AS (SELECT * FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB)"

# ── GLOBAL SIDEBAR FILTERS ────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔽 Global Filters")
    st.caption("Applied across all tabs")

    f_state = st.multiselect("State", [
        "AL","AK","AZ","AR","CA","CO","CT","DC","DE","FL","GA","HI","ID","IL","IN",
        "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH",
        "NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT",
        "VT","VA","WA","WV","WI","WY","PR","VI","GU"
    ])

    f_plan_type = st.multiselect("Plan Type", [
        "HMO","PPO","PFFS","SNP","Cost","MSA","PACE","Demo",
        "Local PPO","Regional PPO","HMO-POS"
    ])

    f_plan_name = st.text_input("Plan Name Contains", placeholder="e.g. Humana, United")
    f_contract_id = st.text_input("Contract ID", placeholder="e.g. H0001")

    enr_col1, enr_col2 = st.columns(2)
    with enr_col1:
        f_enr_min = st.number_input("Min Enrollment", min_value=0, value=0, step=1000)
    with enr_col2:
        f_enr_max = st.number_input("Max Enrollment", min_value=0, value=50000, step=10000,
                                     help="Default 50,000 — focuses on small/regional plans without in-house analytics teams")

    f_stars_max = st.selectbox("Max Overall Stars", ["Any","< 2.0","< 2.5","< 3.0","< 3.5","< 4.0"])
    f_cap_only  = st.checkbox("CAP issues only")
    f_lpi_only  = st.checkbox("Low performers only")

    st.divider()
    st.caption("Filters apply when you click Load/Search buttons")


def build_filter_clause(prefix=""):
    """Build WHERE clause additions from sidebar filters."""
    clauses = []
    p = prefix + "." if prefix else ""

    if f_state:
        states = ",".join([f"'{s}'" for s in f_state])
        clauses.append(f"{p}STATE IN ({states})")
    if f_plan_type:
        types = ",".join([f"'{t}'" for t in f_plan_type])
        clauses.append(f"{p}PLAN_TYPE IN ({types})")
    if f_plan_name:
        clauses.append(f"UPPER({p}ORGANIZATION_MARKETING_NAME) LIKE UPPER('%{f_plan_name}%')")
    if f_contract_id:
        clauses.append(f"UPPER({p}CONTRACT_ID) = UPPER('{f_contract_id}')")
    if f_enr_min > 0:
        clauses.append(f"TRY_TO_NUMBER({p}MBR_CNT) >= {f_enr_min}")
    if f_enr_max > 0:
        clauses.append(f"TRY_TO_NUMBER({p}MBR_CNT) <= {f_enr_max}")
    if f_stars_max != "Any":
        val = float(f_stars_max.replace("< ", ""))
        clauses.append(f"TRY_TO_DECIMAL({p}OVERALL_STARS) < {val}")
    if f_cap_only:
        clauses.append(f"{p}CAP_ISSUE_TYPE IS NOT NULL")
    if f_lpi_only:
        clauses.append(f"{p}REASON_FOR_LPI IS NOT NULL")

    return ("AND " + " AND ".join(clauses)) if clauses else ""


# ── TABS ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "🎯 Opportunities",
    "📋 CAP Enforcement",
    "⚠️ Low Performers",
    "📊 Star Ratings",
    "💊 Measures",
    "📈 Detail Performance",
    "🏢 Contract Directory",
    "🤖 AI Chatbot",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OPPORTUNITIES
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown('<div class="section-header">Consulting Opportunity Scorecard</div>', unsafe_allow_html=True)
    st.caption("CAP issue (+30) · Low performer (+25) · Stars <3.0 (+20) · Stars 3.0–3.4 (+10) · CAI flag (+15)")

    col1, col2 = st.columns(2)
    with col1:
        min_score = st.slider("Min opportunity score", 0, 100, 25, 5)
    with col2:
        limit = st.selectbox("Show top N", [10, 25, 50, 100, 999], index=1)

    if st.button("🔍 Find Opportunities", type="primary", key="opp_btn"):
        with st.spinner("Scoring all plans..."):
            try:
                fc = build_filter_clause()
                df = run_query(f"""
                    {BASE_CTE}
                    SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                           PARENT_ORGANIZATION, STATE, PLAN_TYPE,
                           MBR_CNT AS ENROLLMENT, LEGAL_ENTITY_NAME,
                           CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                           OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                           CASE WHEN REASON_FOR_LPI  IS NOT NULL THEN '✓' ELSE '' END AS LOW_PERFORMER,
                           CASE WHEN CAP_ISSUE_TYPE  IS NOT NULL THEN '✓' ELSE '' END AS HAS_CAP,
                           CAP_ISSUE_TYPE,
                           CASE WHEN OVERALL_FAC     IS NOT NULL THEN '✓' ELSE '' END AS CAI_FLAG,
                           OPPORTUNITY_SCORE,
                           CASE
                               WHEN UPPER(PARENT_ORGANIZATION) SIMILAR TO
                                   '%(HUMANA|UNITED|AETNA|CVS|CENTENE|MOLINA|ANTHEM|BCBS|BLUE CROSS|BLUE SHIELD|KAISER|CIGNA|WELLCARE|ELEVANCE|CARESOURCE|OSCAR|BRIGHT HEALTH)%'
                               THEN '❌ Large National - Skip'
                               WHEN MBR_CNT > 150000 THEN '⚠️ Large - Likely Has Team'
                               WHEN MBR_CNT > 50000  THEN '🟡 Mid-Size - Maybe'
                               ELSE '✅ Small/Regional - Target'
                           END AS ANALYTICS_TEAM_ASSESSMENT
                    FROM BASE
                    WHERE (CASE WHEN CAP_ISSUE_TYPE IS NOT NULL THEN 30 ELSE 0 END
                         + CASE WHEN REASON_FOR_LPI IS NOT NULL THEN 25 ELSE 0 END
                         + CASE WHEN TRY_TO_DECIMAL(OVERALL_STARS) < 3.0 THEN 20
                                WHEN TRY_TO_DECIMAL(OVERALL_STARS) < 3.5 THEN 10 ELSE 0 END
                         + CASE WHEN OVERALL_FAC    IS NOT NULL THEN 15 ELSE 0 END) >= {min_score}
                    {fc}
                    ORDER BY OPPORTUNITY_SCORE DESC LIMIT {limit}
                """)
                c1,c2,c3,c4,c5 = st.columns(5)
                c1.metric("Plans Found", len(df))
                c2.metric("With CAP", int((df["HAS_CAP"]=="✓").sum()))
                c3.metric("Low Performers", int((df["LOW_PERFORMER"]=="✓").sum()))
                try:
                    c4.metric("Avg Stars", f"{pd.to_numeric(df['OVERALL_STARS'],errors='coerce').mean():.2f}")
                    c5.metric("Total Enrollment", f"{pd.to_numeric(df['ENROLLMENT'],errors='coerce').sum():,.0f}")
                except: pass
                st.dataframe(df, use_container_width=True, height=420)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CAP ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown('<div class="section-header">CAP Enforcement Actions</div>', unsafe_allow_html=True)
    cap_view = st.radio("View", ["All CAP Issues","By Parent Organization","By Issue Type","CAP + Stars"], horizontal=True)
    if st.button("📋 Load", key="cap_btn", type="primary"):
        with st.spinner("Loading..."):
            try:
                fc = build_filter_clause()
                if cap_view == "All CAP Issues":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               PARENT_ORGANIZATION, STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
                               CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                               CAP_CONTACT_NAME, CAP_CONTACT_PHONE,
                               OVERALL_STARS, CAP_ISSUE_TYPE, CAP_ISSUE_SUMMARY
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL {fc}
                        ORDER BY OVERALL_STARS ASC"""
                elif cap_view == "By Parent Organization":
                    q = f"""{BASE_CTE} SELECT DISTINCT PARENT_ORGANIZATION,
                               COUNT(DISTINCT CONTRACT_ID) AS CONTRACTS,
                               COUNT(CAP_ISSUE_TYPE) AS CAP_ISSUES,
                               SUM(MBR_CNT) AS TOTAL_ENROLLMENT,
                               MIN(TRY_TO_DECIMAL(OVERALL_STARS)) AS LOWEST_STARS
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL {fc}
                        GROUP BY PARENT_ORGANIZATION ORDER BY CAP_ISSUES DESC"""
                elif cap_view == "By Issue Type":
                    q = f"""{BASE_CTE} SELECT DISTINCT CAP_ISSUE_TYPE,
                               COUNT(*) AS TOTAL, COUNT(DISTINCT CONTRACT_ID) AS CONTRACTS_AFFECTED,
                               SUM(MBR_CNT) AS MEMBERS_AFFECTED
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL {fc}
                        GROUP BY CAP_ISSUE_TYPE ORDER BY TOTAL DESC"""
                else:
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
                               CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                               OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                               CAP_ISSUE_TYPE, CAP_ISSUE_SUMMARY, REASON_FOR_LPI
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL {fc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC"""
                df = run_query(q)
                st.success(f"{len(df)} results")
                st.dataframe(df, use_container_width=True, height=450)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — LOW PERFORMERS
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown('<div class="section-header">CMS Low Performer Plans</div>', unsafe_allow_html=True)
    if st.button("⚠️ Load Low Performers", type="primary", key="lpi_btn"):
        with st.spinner("Loading..."):
            try:
                fc = build_filter_clause()
                df = run_query(f"""{BASE_CTE}
                    SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                           PARENT_ORGANIZATION, STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
                           CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                           OVERALL_STARS, PART_C_STARS, PART_D_STARS, REASON_FOR_LPI,
                           CASE WHEN CAP_ISSUE_TYPE IS NOT NULL THEN 'YES' ELSE 'NO' END AS HAS_CAP,
                           CAP_ISSUE_TYPE, CAP_CONTACT_NAME, CAP_CONTACT_PHONE,
                           CASE WHEN OVERALL_FAC IS NOT NULL THEN 'YES' ELSE 'NO' END AS HAS_CAI_FLAG
                    FROM BASE WHERE REASON_FOR_LPI IS NOT NULL {fc}
                    ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC""")
                st.metric("Low Performer Plans", len(df))
                st.dataframe(df, use_container_width=True)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — STAR RATINGS
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown('<div class="section-header">2026 Star Ratings</div>', unsafe_allow_html=True)
    star_view = st.radio("View", ["All Plans","Below 3.0","Below 3.5","4.0+ Stars","Part C vs D Gap","Domain Stars","High Performers"], horizontal=True)
    if st.button("📊 Load", key="star_btn", type="primary"):
        with st.spinner("Loading..."):
            try:
                fc = build_filter_clause()
                if star_view == "All Plans":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
                               OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                               CASE WHEN REASON_FOR_LPI IS NOT NULL THEN '✓' ELSE '' END AS LOW_PERFORMER,
                               CASE WHEN CAP_ISSUE_TYPE IS NOT NULL THEN '✓' ELSE '' END AS HAS_CAP
                        FROM BASE WHERE 1=1 {fc} ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC"""
                elif star_view == "Below 3.0":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
                               OVERALL_STARS, PART_C_STARS, PART_D_STARS, REASON_FOR_LPI, CAP_ISSUE_TYPE
                        FROM BASE WHERE TRY_TO_DECIMAL(OVERALL_STARS) < 3.0 {fc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC"""
                elif star_view == "Below 3.5":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
                               OVERALL_STARS, PART_C_STARS, PART_D_STARS
                        FROM BASE WHERE TRY_TO_DECIMAL(OVERALL_STARS) < 3.5 {fc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC"""
                elif star_view == "4.0+ Stars":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
                               OVERALL_STARS, PART_C_STARS, PART_D_STARS
                        FROM BASE WHERE TRY_TO_DECIMAL(OVERALL_STARS) >= 4.0 {fc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) DESC"""
                elif star_view == "Part C vs D Gap":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, PART_C_STARS, PART_D_STARS, OVERALL_STARS,
                               ROUND(TRY_TO_DECIMAL(PART_C_STARS)-TRY_TO_DECIMAL(PART_D_STARS),2) AS C_MINUS_D_GAP
                        FROM BASE WHERE 1=1 {fc}
                        ORDER BY ABS(TRY_TO_DECIMAL(PART_C_STARS)-TRY_TO_DECIMAL(PART_D_STARS)) DESC NULLS LAST"""
                elif star_view == "Domain Stars":
                    q = f"SELECT DISTINCT * FROM {DB}.STAR_RATINGS_DOMAIN_STARS ORDER BY 1"
                else:
                    q = f"SELECT DISTINCT * FROM {DB}.STAR_RATINGS_HIGH_PERFORMING_CONTRACTS ORDER BY 1"

                df = run_query(q)
                if star_view not in ["Domain Stars","High Performers"]:
                    c1,c2,c3 = st.columns(3)
                    c1.metric("Plans", len(df))
                    if "ENROLLMENT" in df.columns:
                        c2.metric("Total Enrollment", f"{pd.to_numeric(df['ENROLLMENT'],errors='coerce').sum():,.0f}")
                    if "OVERALL_STARS" in df.columns:
                        c3.metric("Avg Stars", f"{pd.to_numeric(df['OVERALL_STARS'],errors='coerce').mean():.2f}")
                st.dataframe(df, use_container_width=True, height=440)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — MEASURES
# ══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.markdown('<div class="section-header">Measure Performance + 2027 Weights</div>', unsafe_allow_html=True)
    measure_view = st.radio("View", ["Key Measures Summary","Weak Measures (Stars < 3)","2027 Part C Weights","2027 Part D Weights","Measure Crosswalk","Part C Cut Points","Part D Cut Points"], horizontal=True)
    contract_filter = st.text_input("Filter by Contract ID (optional)", placeholder="e.g. H0001", key="meas_contract")
    if st.button("💊 Load", key="meas_btn", type="primary"):
        with st.spinner("Loading..."):
            try:
                fc = build_filter_clause()
                cc = f"AND CONTRACT_ID = '{contract_filter.strip().upper()}'" if contract_filter else ""
                if measure_view == "Key Measures Summary":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT, OVERALL_STARS,
                               C01_DATA AS "C01: Breast Cancer Screening [Data]",
                               C01_STARS AS "C01: Breast Cancer Screening [Stars]",
                               C01_WEIGHT AS "C01 Weight",
                               C02_DATA AS "C02: Colorectal Cancer Screening [Data]",
                               C02_STARS AS "C02: Colorectal Cancer Screening [Stars]",
                               C02_WEIGHT AS "C02 Weight",
                               C03_DATA AS "C03: Annual Flu Vaccine [Data]",
                               C03_STARS AS "C03: Annual Flu Vaccine [Stars]",
                               C03_WEIGHT AS "C03 Weight",
                               C04_DATA AS "C04: Improving Physical Health [Data]",
                               C04_STARS AS "C04: Improving Physical Health [Stars]",
                               C04_WEIGHT AS "C04 Weight",
                               C05_DATA AS "C05: Improving Mental Health [Data]",
                               C05_STARS AS "C05: Improving Mental Health [Stars]",
                               C05_WEIGHT AS "C05 Weight",
                               C12_DATA AS "C12: Blood Sugar Controlled [Data]",
                               C12_STARS AS "C12: Blood Sugar Controlled [Stars]",
                               C12_WEIGHT AS "C12 Weight",
                               C14_DATA AS "C14: Controlling Blood Pressure [Data]",
                               C14_STARS AS "C14: Controlling Blood Pressure [Stars]",
                               C14_WEIGHT AS "C14 Weight",
                               C18_DATA AS "C18: Plan All-Cause Readmissions [Data]",
                               C18_STARS AS "C18: Plan All-Cause Readmissions [Stars]",
                               C18_WEIGHT AS "C18 Weight",
                               D08_DATA AS "D08: Med Adherence Diabetes [Data]",
                               D08_STARS AS "D08: Med Adherence Diabetes [Stars]",
                               D08_WEIGHT AS "D08 Weight",
                               D09_DATA AS "D09: Med Adherence Hypertension [Data]",
                               D09_STARS AS "D09: Med Adherence Hypertension [Stars]",
                               D09_WEIGHT AS "D09 Weight",
                               D10_DATA AS "D10: Med Adherence Cholesterol [Data]",
                               D10_STARS AS "D10: Med Adherence Cholesterol [Stars]",
                               D10_WEIGHT AS "D10 Weight"
                        FROM BASE WHERE 1=1 {fc} {cc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC LIMIT 200"""
                elif measure_view == "Weak Measures (Stars < 3)":
                    q = f"""{BASE_CTE} SELECT DISTINCT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, PLAN_TYPE, OVERALL_STARS,
                               CASE WHEN TRY_TO_DECIMAL(C01_STARS)<3 THEN C01_STARS END AS "C01: Breast Cancer Screening",
                               CASE WHEN TRY_TO_DECIMAL(C02_STARS)<3 THEN C02_STARS END AS "C02: Colorectal Cancer Screening",
                               CASE WHEN TRY_TO_DECIMAL(C03_STARS)<3 THEN C03_STARS END AS "C03: Annual Flu Vaccine",
                               CASE WHEN TRY_TO_DECIMAL(C12_STARS)<3 THEN C12_STARS END AS "C12: Blood Sugar Controlled",
                               CASE WHEN TRY_TO_DECIMAL(C14_STARS)<3 THEN C14_STARS END AS "C14: Controlling Blood Pressure",
                               CASE WHEN TRY_TO_DECIMAL(C18_STARS)<3 THEN C18_STARS END AS "C18: Plan All-Cause Readmissions",
                               CASE WHEN TRY_TO_DECIMAL(D08_STARS)<3 THEN D08_STARS END AS "D08: Med Adherence Diabetes",
                               CASE WHEN TRY_TO_DECIMAL(D09_STARS)<3 THEN D09_STARS END AS "D09: Med Adherence Hypertension",
                               CASE WHEN TRY_TO_DECIMAL(D10_STARS)<3 THEN D10_STARS END AS "D10: Med Adherence Cholesterol"
                        FROM BASE WHERE 1=1 {fc} {cc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC LIMIT 200"""
                elif measure_view == "2027 Part C Weights":
                    q = f"""SELECT DISTINCT
                               MEASURE_NAME,
                               WEIGHTING_CATEGORY,
                               PART_C_SUMMARY_AND_MA_PD_OVERALL_WEIGHT AS WEIGHT
                            FROM {DB}.STAR_RATINGS_2027_PART_C_MEASURES
                            ORDER BY TRY_TO_NUMBER(PART_C_SUMMARY_AND_MA_PD_OVERALL_WEIGHT) DESC"""
                elif measure_view == "2027 Part D Weights":
                    q = f"""SELECT DISTINCT
                               MEASURE_NAME,
                               WEIGHTING_CATEGORY,
                               PART_D_SUMMARY_AND_MA_PD_OVERALL_WEIGHT AS WEIGHT
                            FROM {DB}.STAR_RATINGS_2027_PART_D_MEASURES
                            ORDER BY TRY_TO_NUMBER(PART_D_SUMMARY_AND_MA_PD_OVERALL_WEIGHT) DESC"""
                elif measure_view == "Measure Crosswalk":
                    q = f"SELECT DISTINCT * FROM {DB}.STAR_RATINGS_MEASURE_CROSSWALK ORDER BY COLUMN_INDEX"
                elif measure_view == "Part C Cut Points":
                    q = f"SELECT DISTINCT * FROM {DB}.STAR_RATINGS_PART_C_CUT_POINTS"
                else:
                    q = f"SELECT DISTINCT * FROM {DB}.STAR_RATINGS_PART_D_CUT_POINTS"
                df = run_query(q)
                st.success(f"{len(df)} rows")
                st.dataframe(df, use_container_width=True, height=450)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — DETAIL PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════
with tab6:
    st.markdown('<div class="section-header">Detail Performance Data</div>', unsafe_allow_html=True)
    st.caption("Full measure data and stars for every plan — data value, star score, and 2027 weight side by side")

    perf_col1, perf_col2, perf_col3 = st.columns(3)
    with perf_col1:
        perf_type = st.radio("Measure set", ["Part C", "Part D", "Both"], horizontal=True)
    with perf_col2:
        perf_view = st.radio("Show", ["Data + Stars + Weight", "Stars only", "Data only"], horizontal=True)
    with perf_col3:
        perf_sort = st.selectbox("Sort by", ["Overall Stars ↑", "Overall Stars ↓", "Enrollment ↓", "Plan Name"])

    sort_map = {
        "Overall Stars ↑": "TRY_TO_DECIMAL(OVERALL_STARS) ASC",
        "Overall Stars ↓": "TRY_TO_DECIMAL(OVERALL_STARS) DESC",
        "Enrollment ↓": "TRY_TO_NUMBER(MBR_CNT) DESC",
        "Plan Name": "ORGANIZATION_MARKETING_NAME ASC",
    }

    if st.button("📈 Load Detail Performance", type="primary", key="perf_btn"):
        with st.spinner("Loading full measure detail..."):
            try:
                fc = build_filter_clause()

                # Build measure columns based on selection
                c_measures = [("C01","Breast Cancer Screening",1),("C02","Colorectal Cancer Screening",1),("C03","Annual Flu Vaccine",1),("C04","Improving or Maintaining Physical Health",3),("C05","Improving or Maintaining Mental Health",3),("C06","Monitoring Physical Activity",1),("C07","Special Needs Plan (SNP) Care Management",1),("C08","Care for Older Adults - Medication Review",1),("C09","Care for Older Adults - Pain Assessment","NULL"),("C10","Osteoporosis Management in Women Who Had a Fracture",1),("C11","Diabetes Care - Eye Exam",1),("C12","Diabetes Care - Blood Sugar Controlled",3),("C13","Kidney Health Evaluation for Patients with Diabetes",1),("C14","Controlling High Blood Pressure",3),("C15","Reducing the Risk of Falling",1),("C16","Improving Bladder Control",1),("C17","Medication Reconciliation Post-Discharge","NULL"),("C18","Plan All-Cause Readmissions",3),("C19","Statin Therapy for Patients with Cardiovascular Disease",1),("C20","Transitions of Care",1),("C21","Follow-up After ED Visit for Multiple High-Risk Chronic Conditions",1),("C22","Getting Needed Care",2),("C23","Getting Appointments and Care Quickly",2),("C24","Customer Service",2),("C25","Rating of Health Care Quality",2),("C26","Rating of Health Plan",2),("C27","Care Coordination",2),("C28","Complaints About the Health Plan",2),("C29","Members Choosing to Leave the Plan",2),("C30","Health Plan Quality Improvement",5),("C31","Plan Makes Timely Decisions About Appeals",2),("C32","Reviewing Appeals Decisions",2),("C33","Call Center Foreign Language Interpreter and TTY Availability",2)]
                d_measures = [("D01","Call Center Foreign Language Interpreter and TTY Availability (Part D)",2),("D02","Complaints About the Drug Plan",2),("D03","Members Choosing to Leave the Plan (Part D)",2),("D04","Drug Plan Quality Improvement",5),("D05","Rating of Drug Plan",2),("D06","Getting Needed Prescription Drugs",2),("D07","MPF Price Accuracy",1),("D08","Medication Adherence for Diabetes Medications",3),("D09","Medication Adherence for Hypertension (RAS Antagonists)",3),("D10","Medication Adherence for Cholesterol (Statins)",3),("D11","MTM Program Completion Rate for CMR","NULL"),("D12","Statin Use in Persons with Diabetes (SUPD)",1)]

                measures = []
                if perf_type in ["Part C", "Both"]: measures += c_measures
                if perf_type in ["Part D", "Both"]: measures += d_measures

                # Load live weights from Snowflake 2027 measure tables
                sf_weights = load_measure_weights()

                meas_cols = []
                for code, name, weight in measures:
                    # Use live weight from Snowflake if available, else fall back to hardcoded
                    live_w = sf_weights.get(name)
                    w_label = live_w if live_w is not None else ("N/A" if str(weight) == "NULL" else weight)
                    if perf_view in ["Data + Stars + Weight", "Data only"]:
                        meas_cols.append(f'{code}_DATA AS "{code}: {name} [Data] (W:{w_label})"')
                    if perf_view in ["Data + Stars + Weight", "Stars only"]:
                        meas_cols.append(f'{code}_STARS AS "{code}: {name} [Stars]"')

                meas_sql = ", ".join(meas_cols)

                q = f"""{BASE_CTE}
                    SELECT DISTINCT CONTRACT_ID,
                           ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                           STATE, PLAN_TYPE,
                           MBR_CNT AS ENROLLMENT,
                           OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                           {meas_sql}
                    FROM BASE
                    WHERE 1=1 {fc}
                    ORDER BY {sort_map[perf_sort]}
                    LIMIT 500"""

                df = run_query(q)
                c1,c2,c3 = st.columns(3)
                c1.metric("Plans", len(df))
                c2.metric("Measures Shown", len(measures))
                c3.metric("Total Enrollment", f"{pd.to_numeric(df['ENROLLMENT'],errors='coerce').sum():,.0f}")

                st.dataframe(df, use_container_width=True, height=500)

                # Download button
                csv = df.to_csv(index=False)
                st.download_button("⬇️ Download as CSV", csv, "detail_performance.csv", "text/csv")

            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — CONTRACT DIRECTORY
# ══════════════════════════════════════════════════════════════════════════════
with tab7:
    st.markdown('<div class="section-header">MA Contract Directory 2026</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        dir_search = st.text_input("Search plan name or contract number", placeholder="e.g. Humana or H0001")
    with col2:
        dir_state = st.text_input("State (optional)", placeholder="e.g. CA")
    if st.button("🔍 Search", type="primary", key="dir_btn"):
        with st.spinner("Searching..."):
            try:
                where = []
                if dir_search:
                    where.append(f"(UPPER(ORGANIZATION_MARKETING_NAME) LIKE UPPER('%{dir_search}%') OR UPPER(CONTRACT_NUMBER) LIKE UPPER('%{dir_search}%'))")
                if dir_state:
                    where.append(f"UPPER(LEGAL_ENTITY_STATE_CODE) = UPPER('{dir_state}')")
                where_str = "WHERE " + " AND ".join(where) if where else ""
                df = run_query(f"SELECT DISTINCT * FROM {DB}.MA_CONTRACT_DIRECTORY_2026_04 {where_str} LIMIT 300")
                st.success(f"{len(df)} contracts found")
                st.dataframe(df, use_container_width=True, height=450)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — AI CHATBOT
# ══════════════════════════════════════════════════════════════════════════════
with tab8:
    st.markdown('<div class="section-header">MA Consulting AI Chatbot</div>', unsafe_allow_html=True)
    st.caption("Ask in plain English — the AI queries your live Snowflake data and gives you exact results")

    # Schema context for SQL generation
    SCHEMA_CONTEXT = """
    Snowflake CTE BASE columns: CONTRACT_ID, ORGANIZATION_MARKETING_NAME, PARENT_ORGANIZATION,
    MBR_CNT (enrollment), STATE, PLAN_TYPE, CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
    OVERALL_STARS, PART_C_STARS, PART_D_STARS, REASON_FOR_LPI (NULL=not low performer),
    OVERALL_FAC (NULL=no CAI flag), CAP_ISSUE_TYPE (NULL=no CAP), CAP_ISSUE_SUMMARY,
    CAP_CONTACT_NAME, CAP_CONTACT_PHONE, OPPORTUNITY_SCORE (pre-computed).
    Key measures: C12=Blood Sugar(3), C14=Blood Pressure(3), C18=Readmissions(3), C30=Quality Improvement(5),
    D08=Med Adherence Diabetes(3), D09=Hypertension(3), D10=Cholesterol(3), D04=Drug Quality(5).

    CRITICAL - CONSULTING FIT LOGIC (always apply when generating SQL):
    EXCLUDE from results any plan where PARENT_ORGANIZATION ILIKE any of:
    '%Humana%','%United%','%UnitedHealth%','%Aetna%','%CVS%','%Centene%','%Molina%',
    '%Anthem%','%BCBS%','%Blue Cross%','%Blue Shield%','%Kaiser%','%Cigna%',
    '%WellCare%','%Elevance%','%CareSource%','%Oscar%','%Bright Health%'
    These companies have large in-house analytics teams and are NOT consulting targets.

    IDEAL TARGET PROFILE:
    - Independent or regional parent organization
    - MBR_CNT < 50000 (small enough to need outside help)
    - OVERALL_STARS < 3.5 OR CAP_ISSUE_TYPE IS NOT NULL OR REASON_FOR_LPI IS NOT NULL
    Always add these filters to SQL unless user explicitly asks for large plans.
    """

    BASE_CTE_FOR_CHAT = "WITH BASE AS (SELECT * FROM MA_ANALYTICS.DATA_PROCESSING.VW_MA_INTELLIGENCE_HUB)"

    SQL_GEN_PROMPT = f"""You are a Snowflake SQL expert generating queries for Sadaf's MA consulting firm.
She targets ONLY small independent plans without in-house analytics teams.

{SCHEMA_CONTEXT}

ALWAYS use this CTE: {BASE_CTE_FOR_CHAT}

ALWAYS include these columns:
CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME, PARENT_ORGANIZATION,
STATE, PLAN_TYPE, MBR_CNT AS ENROLLMENT,
CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
OVERALL_STARS, PART_C_STARS, PART_D_STARS, OPPORTUNITY_SCORE,
CASE WHEN UPPER(PARENT_ORGANIZATION) SIMILAR TO '%(HUMANA|UNITED|AETNA|CVS|CENTENE|MOLINA|ANTHEM|BCBS|BLUE CROSS|BLUE SHIELD|KAISER|CIGNA|WELLCARE|ELEVANCE)%'
     THEN 'Large National - Skip'
     WHEN MBR_CNT > 150000 THEN 'Large - Likely Has Team'
     WHEN MBR_CNT > 50000  THEN 'Mid-Size - Maybe'
     ELSE 'Small/Regional - TARGET'
END AS CONSULTING_FIT

UNLESS user explicitly asks for large plans, ALWAYS add:
AND NOT (UPPER(PARENT_ORGANIZATION) SIMILAR TO '%(HUMANA|UNITED|AETNA|CVS|CENTENE|MOLINA|ANTHEM|BCBS|BLUE CROSS|BLUE SHIELD|KAISER|CIGNA|WELLCARE|ELEVANCE)%')
AND MBR_CNT < 150000

For opportunities: ORDER BY OPPORTUNITY_SCORE DESC LIMIT 20
For CAP: AND CAP_ISSUE_TYPE IS NOT NULL
For low performers: AND REASON_FOR_LPI IS NOT NULL
Always LIMIT 50 max. Return ONLY SQL — no explanation, no markdown, no backticks.

Question: """

    # Quick questions
    st.write("**Quick questions:**")
    quick_qs = [
        "Top 10 small independent plans to target for consulting",
        "Small plans with CAP issues — show contact details",
        "Low performer plans that are small and independent",
        "Plans below 3 stars with under 50,000 members",
        "Small plans with worst medication adherence scores",
        "Independent plans in California with low stars",
        "Small plans with both CAP issues and low stars",
        "Regional plans with CAI flags — no large parent org",
        "Which small plans have the highest opportunity score?",
    ]
    cols = st.columns(3)
    for i, q in enumerate(quick_qs):
        if cols[i % 3].button(q, use_container_width=True, key=f"cq{i}"):
            st.session_state.setdefault("chat_messages", [])
            if not st.session_state.chat_messages or st.session_state.chat_messages[-1]["content"] != q:
                st.session_state.chat_messages.append({"role": "user", "content": q})
                st.session_state.chat_run = True

    st.divider()

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    # Display history
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            if isinstance(msg["content"], pd.DataFrame):
                st.dataframe(msg["content"], use_container_width=True)
            else:
                st.markdown(msg["content"])

    # Chat input
    if user_prompt := st.chat_input("Ask about your MA plans — get real data back..."):
        st.session_state.chat_messages.append({"role": "user", "content": user_prompt})
        with st.chat_message("user"):
            st.markdown(user_prompt)
        st.session_state.chat_run = True

    # Process
    if st.session_state.get("chat_run") and st.session_state.chat_messages:
        st.session_state.chat_run = False
        last_q = st.session_state.chat_messages[-1]["content"]
        if not isinstance(last_q, pd.DataFrame):

            with st.chat_message("assistant"):
                with st.spinner("Generating SQL and querying your data..."):
                    try:
                        conn = get_connection()
                        cur = conn.cursor()

                        # Step 1: Generate SQL using faster mistral-large (not large2)
                        sql_request = SQL_GEN_PROMPT + last_q
                        sql_request_esc = sql_request.replace("\\", "\\\\").replace("'", "\\'")
                        cur.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{sql_request_esc}') AS SQL_QUERY")
                        raw_sql_response = cur.fetchone()[0]

                        # Parse SQL
                        try:
                            parsed = json.loads(raw_sql_response)
                            generated_sql = parsed["choices"][0]["message"]["content"].strip()
                        except Exception:
                            generated_sql = raw_sql_response.strip()

                        generated_sql = generated_sql.replace("```sql", "").replace("```", "").strip()

                        # Step 2: Run SQL
                        try:
                            cur.execute(generated_sql)
                            cols = [c[0] for c in cur.description]
                            rows = cur.fetchall()
                            result_df = pd.DataFrame(rows, columns=cols)

                            st.success(f"Found {len(result_df)} results from your live Snowflake data")
                            st.dataframe(result_df, use_container_width=True, height=350)
                            st.session_state.chat_messages.append({"role": "assistant", "content": result_df})

                            # Step 3: Explain — only top 5 rows, short prompt, no JSON parsing
                            if len(result_df) > 0:
                                data_preview = result_df.head(5).to_string(index=False)
                                explain_req = (
                                    f"{EXPLAIN_PROMPT}\n\n"
                                    f"Question: {last_q}\n\nTop results (showing up to 5):\n{data_preview}"
                                )
                                explain_esc = explain_req.replace("\\", "\\\\").replace("'", "\\'")
                                cur.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{explain_esc}') AS EXPLANATION")
                                raw_exp = cur.fetchone()[0]
                                try:
                                    parsed_exp = json.loads(raw_exp)
                                    explanation = parsed_exp["choices"][0]["message"]["content"]
                                except Exception:
                                    explanation = raw_exp
                                st.markdown("**💡 Key Takeaways:**")
                                st.markdown(explanation)
                                st.session_state.chat_messages.append({"role": "assistant", "content": "**💡 Key Takeaways:**\n" + explanation})

                            # Download
                            csv = result_df.to_csv(index=False)
                            st.download_button("⬇️ Download results", csv, "chat_results.csv", "text/csv", key=f"dl_{len(st.session_state.chat_messages)}")

                        except Exception as sql_err:
                            st.warning(f"Could not run SQL. Answering from general knowledge...")
                            fallback_req = f"You are an MA consulting analyst. Answer very briefly (3 bullet points max): {last_q}"
                            fallback_esc = fallback_req.replace("\\", "\\\\").replace("'", "\\'")
                            cur.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{fallback_esc}') AS ANSWER")
                            raw_fb = cur.fetchone()[0]
                            try:
                                parsed_fb = json.loads(raw_fb)
                                fb_answer = parsed_fb["choices"][0]["message"]["content"]
                            except Exception:
                                fb_answer = raw_fb
                            st.markdown(fb_answer)
                            st.session_state.chat_messages.append({"role": "assistant", "content": fb_answer})

                    except Exception as e:
                        st.error(f"Error: {e}")

    if st.session_state.get("chat_messages"):
        if st.button("🗑️ Clear conversation", key="clear_chat"):
            st.session_state.chat_messages = []
            st.rerun()
