import streamlit as st
import pandas as pd
import snowflake.connector

st.set_page_config(page_title="MA Intelligence Hub", page_icon="★", layout="wide")

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size:1.8rem; color:#1F4E79; font-weight:600; }
[data-testid="stMetricLabel"] { font-size:0.72rem; color:#666; }
.section-header { font-size:1.05rem; font-weight:600; color:#1F4E79;
    border-bottom:2px solid #1F4E79; padding-bottom:4px; margin:0.8rem 0 0.5rem; }
</style>""", unsafe_allow_html=True)

st.title("★ MA Intelligence Hub")
st.caption("Star Ratings · CAP Enforcement · Low Performers · Measure Performance · Enrollment · Contract Directory")

# ── SNOWFLAKE CONNECTION ───────────────────────────────────────────────────────
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

DB = "MA_ANALYTICS.DATA_PROCESSING"

# ── BASE CTE ──────────────────────────────────────────────────────────────────
BASE_CTE = f"""
WITH BASE AS (
    SELECT
        A.CONTRACT_ID,
        A.ORGANIZATION_MARKETING_NAME,
        A.PARENT_ORGANIZATION,
        SUM(TRY_TO_NUMBER(B.ENROLLMENT))        AS MBR_CNT,
        MAX(B.LEGAL_ENTITY_NAME)                AS LEGAL_ENTITY_NAME,
        MAX(B.LEGAL_ENTITY_STATE_CODE)          AS STATE,
        MAX(B.DIRECTORY_CONTACT_FIRST_NAME)     AS CONTACT_FIRST_NAME,
        MAX(B.DIRECTORY_CONTACT_LAST_NAME)      AS CONTACT_LAST_NAME,
        MAX(B.DIRECTORY_CONTACT_PHONE)          AS CONTACT_PHONE,
        MAX(B.DIRECTORY_CONTACT_EMAIL)          AS CONTACT_EMAIL,
        S."2026_PART_C_SUMMARY"                 AS PART_C_STARS,
        S."2026_PART_D_SUMMARY"                 AS PART_D_STARS,
        S."2026_OVERALL"                        AS OVERALL_STARS,
        L.REASON_FOR_LPI,
        CAI.PART_C_FAC,
        CAI.PART_D_MAPD_FAC,
        CAI.OVERALL_FAC,
        CAP."Issue_Type"                        AS CAP_ISSUE_TYPE,
        CAP."Issue_Summary"                     AS CAP_ISSUE_SUMMARY,
        CAP."Organization_Contact_Name"         AS CAP_CONTACT_NAME,
        CAP."Organization_Contact_Phone"        AS CAP_CONTACT_PHONE,
        A.C01_BREAST_CANCER_SCREENING                        AS C01_DATA, E.C01_BREAST_CANCER_SCREENING                        AS C01_STARS, 1 AS C01_WEIGHT,
        A.C02_COLORECTAL_CANCER_SCREENING                    AS C02_DATA, E.C02_COLORECTAL_CANCER_SCREENING                    AS C02_STARS, 1 AS C02_WEIGHT,
        A.C03_ANNUAL_FLU_VACCINE                             AS C03_DATA, E.C03_ANNUAL_FLU_VACCINE                             AS C03_STARS, 1 AS C03_WEIGHT,
        A.C04_IMPROVING_OR_MAINTAINING_PHYSICAL_HEALTH       AS C04_DATA, E.C04_IMPROVING_OR_MAINTAINING_PHYSICAL_HEALTH       AS C04_STARS, 3 AS C04_WEIGHT,
        A.C05_IMPROVING_OR_MAINTAINING_MENTAL_HEALTH         AS C05_DATA, E.C05_IMPROVING_OR_MAINTAINING_MENTAL_HEALTH         AS C05_STARS, 3 AS C05_WEIGHT,
        A.C06_MONITORING_PHYSICAL_ACTIVITY                   AS C06_DATA, E.C06_MONITORING_PHYSICAL_ACTIVITY                   AS C06_STARS, 1 AS C06_WEIGHT,
        A.C07_SPECIAL_NEEDS_PLAN_SNP_CARE_MANAGEMENT         AS C07_DATA, E.C07_SPECIAL_NEEDS_PLAN_SNP_CARE_MANAGEMENT         AS C07_STARS, 1 AS C07_WEIGHT,
        A.C08_CARE_FOR_OLDER_ADULTS_MEDICATION_REVIEW        AS C08_DATA, E.C08_CARE_FOR_OLDER_ADULTS_MEDICATION_REVIEW        AS C08_STARS, 1 AS C08_WEIGHT,
        A.C09_CARE_FOR_OLDER_ADULTS_PAIN_ASSESSMENT          AS C09_DATA, E.C09_CARE_FOR_OLDER_ADULTS_PAIN_ASSESSMENT          AS C09_STARS, NULL AS C09_WEIGHT,
        A.C10_OSTEOPOROSIS_MANAGEMENT_IN_WOMEN_WHO_HAD_A_FRACTURE AS C10_DATA, E.C10_OSTEOPOROSIS_MANAGEMENT_IN_WOMEN_WHO_HAD_A_FRACTURE AS C10_STARS, 1 AS C10_WEIGHT,
        A.C11_DIABETES_CARE_EYE_EXAM                         AS C11_DATA, E.C11_DIABETES_CARE_EYE_EXAM                         AS C11_STARS, 1 AS C11_WEIGHT,
        A.C12_DIABETES_CARE_BLOOD_SUGAR_CONTROLLED           AS C12_DATA, E.C12_DIABETES_CARE_BLOOD_SUGAR_CONTROLLED           AS C12_STARS, 3 AS C12_WEIGHT,
        A.C13_KIDNEY_HEALTH_EVALUATION_FOR_PATIENTS_WITH_DIABETES AS C13_DATA, E.C13_KIDNEY_HEALTH_EVALUATION_FOR_PATIENTS_WITH_DIABETES AS C13_STARS, 1 AS C13_WEIGHT,
        A.C14_CONTROLLING_HIGH_BLOOD_PRESSURE                AS C14_DATA, E.C14_CONTROLLING_HIGH_BLOOD_PRESSURE                AS C14_STARS, 3 AS C14_WEIGHT,
        A.C15_REDUCING_THE_RISK_OF_FALLING                   AS C15_DATA, E.C15_REDUCING_THE_RISK_OF_FALLING                   AS C15_STARS, 1 AS C15_WEIGHT,
        A.C16_IMPROVING_BLADDER_CONTROL                      AS C16_DATA, E.C16_IMPROVING_BLADDER_CONTROL                      AS C16_STARS, 1 AS C16_WEIGHT,
        A.C17_MEDICATION_RECONCILIATION_POST_DISCHARGE       AS C17_DATA, E.C17_MEDICATION_RECONCILIATION_POST_DISCHARGE       AS C17_STARS, NULL AS C17_WEIGHT,
        A.C18_PLAN_ALL_CAUSE_READMISSIONS                    AS C18_DATA, E.C18_PLAN_ALL_CAUSE_READMISSIONS                    AS C18_STARS, 3 AS C18_WEIGHT,
        A.C19_STATIN_THERAPY_FOR_PATIENTS_WITH_CARDIOVASCULAR_DISEASE AS C19_DATA, E.C19_STATIN_THERAPY_FOR_PATIENTS_WITH_CARDIOVASCULAR_DISEASE AS C19_STARS, 1 AS C19_WEIGHT,
        A.C20_TRANSITIONS_OF_CARE                            AS C20_DATA, E.C20_TRANSITIONS_OF_CARE                            AS C20_STARS, 1 AS C20_WEIGHT,
        A.C21_FOLLOW_UP_AFTER_EMERGENCY_DEPARTMENT_VISIT_FOR_PEOPLE_WITH_MULTIPLE_HIGH_RISK_CHRONIC_CONDITIONS AS C21_DATA, E.C21_FOLLOW_UP_AFTER_EMERGENCY_DEPARTMENT_VISIT_FOR_PEOPLE_WITH_MULTIPLE_HIGH_RISK_CHRONIC_CONDITIONS AS C21_STARS, 1 AS C21_WEIGHT,
        A.C22_GETTING_NEEDED_CARE                            AS C22_DATA, E.C22_GETTING_NEEDED_CARE                            AS C22_STARS, 2 AS C22_WEIGHT,
        A.C23_GETTING_APPOINTMENTS_AND_CARE_QUICKLY          AS C23_DATA, E.C23_GETTING_APPOINTMENTS_AND_CARE_QUICKLY          AS C23_STARS, 2 AS C23_WEIGHT,
        A.C24_CUSTOMER_SERVICE                               AS C24_DATA, E.C24_CUSTOMER_SERVICE                               AS C24_STARS, 2 AS C24_WEIGHT,
        A.C25_RATING_OF_HEALTH_CARE_QUALITY                  AS C25_DATA, E.C25_RATING_OF_HEALTH_CARE_QUALITY                  AS C25_STARS, 2 AS C25_WEIGHT,
        A.C26_RATING_OF_HEALTH_PLAN                          AS C26_DATA, E.C26_RATING_OF_HEALTH_PLAN                          AS C26_STARS, 2 AS C26_WEIGHT,
        A.C27_CARE_COORDINATION                              AS C27_DATA, E.C27_CARE_COORDINATION                              AS C27_STARS, 2 AS C27_WEIGHT,
        A.C28_COMPLAINTS_ABOUT_THE_HEALTH_PLAN               AS C28_DATA, E.C28_COMPLAINTS_ABOUT_THE_HEALTH_PLAN               AS C28_STARS, 2 AS C28_WEIGHT,
        A.C29_MEMBERS_CHOOSING_TO_LEAVE_THE_PLAN             AS C29_DATA, E.C29_MEMBERS_CHOOSING_TO_LEAVE_THE_PLAN             AS C29_STARS, 2 AS C29_WEIGHT,
        A.C30_HEALTH_PLAN_QUALITY_IMPROVEMENT                AS C30_DATA, E.C30_HEALTH_PLAN_QUALITY_IMPROVEMENT                AS C30_STARS, 5 AS C30_WEIGHT,
        A.C31_PLAN_MAKES_TIMELY_DECISIONS_ABOUT_APPEALS      AS C31_DATA, E.C31_PLAN_MAKES_TIMELY_DECISIONS_ABOUT_APPEALS      AS C31_STARS, 2 AS C31_WEIGHT,
        A.C32_REVIEWING_APPEALS_DECISIONS                    AS C32_DATA, E.C32_REVIEWING_APPEALS_DECISIONS                    AS C32_STARS, 2 AS C32_WEIGHT,
        A.C33_CALL_CENTER_FOREIGN_LANGUAGE_INTERPRETER_AND_TTY_AVAILABILITY AS C33_DATA, E.C33_CALL_CENTER_FOREIGN_LANGUAGE_INTERPRETER_AND_TTY_AVAILABILITY AS C33_STARS, 2 AS C33_WEIGHT,
        A.D01_CALL_CENTER_FOREIGN_LANGUAGE_INTERPRETER_AND_TTY_AVAILABILITY AS D01_DATA, E.D01_CALL_CENTER_FOREIGN_LANGUAGE_INTERPRETER_AND_TTY_AVAILABILITY AS D01_STARS, 2 AS D01_WEIGHT,
        A.D02_COMPLAINTS_ABOUT_THE_DRUG_PLAN                 AS D02_DATA, E.D02_COMPLAINTS_ABOUT_THE_DRUG_PLAN                 AS D02_STARS, 2 AS D02_WEIGHT,
        A.D03_MEMBERS_CHOOSING_TO_LEAVE_THE_PLAN             AS D03_DATA, E.D03_MEMBERS_CHOOSING_TO_LEAVE_THE_PLAN             AS D03_STARS, 2 AS D03_WEIGHT,
        A.D04_DRUG_PLAN_QUALITY_IMPROVEMENT                  AS D04_DATA, E.D04_DRUG_PLAN_QUALITY_IMPROVEMENT                  AS D04_STARS, 5 AS D04_WEIGHT,
        A.D05_RATING_OF_DRUG_PLAN                            AS D05_DATA, E.D05_RATING_OF_DRUG_PLAN                            AS D05_STARS, 2 AS D05_WEIGHT,
        A.D06_GETTING_NEEDED_PRESCRIPTION_DRUGS              AS D06_DATA, E.D06_GETTING_NEEDED_PRESCRIPTION_DRUGS              AS D06_STARS, 2 AS D06_WEIGHT,
        A.D07_MPF_PRICE_ACCURACY                             AS D07_DATA, E.D07_MPF_PRICE_ACCURACY                             AS D07_STARS, 1 AS D07_WEIGHT,
        A.D08_MEDICATION_ADHERENCE_FOR_DIABETES_MEDICATIONS  AS D08_DATA, E.D08_MEDICATION_ADHERENCE_FOR_DIABETES_MEDICATIONS  AS D08_STARS, 3 AS D08_WEIGHT,
        A.D09_MEDICATION_ADHERENCE_FOR_HYPERTENSION_RAS_ANTAGONISTS AS D09_DATA, E.D09_MEDICATION_ADHERENCE_FOR_HYPERTENSION_RAS_ANTAGONISTS AS D09_STARS, 3 AS D09_WEIGHT,
        A.D10_MEDICATION_ADHERENCE_FOR_CHOLESTEROL_STATINS   AS D10_DATA, E.D10_MEDICATION_ADHERENCE_FOR_CHOLESTEROL_STATINS   AS D10_STARS, 3 AS D10_WEIGHT,
        A.D11_MTM_PROGRAM_COMPLETION_RATE_FOR_CMR            AS D11_DATA, E.D11_MTM_PROGRAM_COMPLETION_RATE_FOR_CMR            AS D11_STARS, NULL AS D11_WEIGHT,
        A.D12_STATIN_USE_IN_PERSONS_WITH_DIABETES_SUPD       AS D12_DATA, E.D12_STATIN_USE_IN_PERSONS_WITH_DIABETES_SUPD       AS D12_STARS, 1 AS D12_WEIGHT
    FROM {DB}.STAR_RATINGS_MEASURE_DATA A
    INNER JOIN {DB}.MA_CONTRACT_DIRECTORY_2026_04 B  ON TRIM(A.CONTRACT_ID) = TRIM(B.CONTRACT_NUMBER)
    INNER JOIN {DB}.STAR_RATINGS_MEASURE_STARS    E  ON TRIM(A.CONTRACT_ID) = TRIM(E.CONTRACT_ID)
    LEFT  JOIN {DB}.STAR_RATINGS_SUMMARY_RATINGS  S  ON TRIM(A.CONTRACT_ID) = TRIM(S.CONTRACT_NUMBER)
    LEFT  JOIN {DB}.STAR_RATINGS_LOW_PERFORMING_CONTRACTS L  ON TRIM(A.CONTRACT_ID) = TRIM(L.CONTRACT_NUMBER)
    LEFT  JOIN {DB}.STAR_RATINGS_CAI              CAI ON TRIM(A.CONTRACT_ID) = TRIM(CAI.CONTRACT_NUMBER)
    LEFT  JOIN {DB}.ADHOC_CAP_SUMMARY             CAP ON TRIM(A.CONTRACT_ID) = TRIM(CAP."Contract_ID")
    WHERE TRY_TO_NUMBER(B.ENROLLMENT) IS NOT NULL
    GROUP BY ALL
)
"""

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🎯 Opportunities", "📋 CAP Enforcement", "⚠️ Low Performers",
    "📊 Star Ratings", "💊 Measures", "🏢 Contract Directory", "🤖 AI Assistant",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OPPORTUNITIES
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown('<div class="section-header">Consulting Opportunity Scorecard</div>', unsafe_allow_html=True)
    st.caption("CAP issue (+30) · Low performer (+25) · Stars <3.0 (+20) · Stars 3.0–3.4 (+10) · CAI flag (+15)")
    col1, col2, col3 = st.columns(3)
    with col1:
        min_score = st.slider("Min opportunity score", 0, 100, 25, 5)
    with col2:
        star_max = st.selectbox("Max overall stars", ["Any","< 2.0","< 2.5","< 3.0","< 3.5","< 4.0"])
    with col3:
        limit = st.selectbox("Show top N", [10, 25, 50, 100, 999], index=1)

    star_clause = ""
    if star_max != "Any":
        val = float(star_max.replace("< ", ""))
        star_clause = f"AND TRY_TO_DECIMAL(OVERALL_STARS) < {val}"

    if st.button("🔍 Find Opportunities", type="primary"):
        with st.spinner("Scoring all plans..."):
            try:
                df = run_query(f"""
                    {BASE_CTE}
                    SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                           PARENT_ORGANIZATION, STATE, MBR_CNT AS ENROLLMENT,
                           LEGAL_ENTITY_NAME,
                           CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                           OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                           CASE WHEN REASON_FOR_LPI  IS NOT NULL THEN '✓' ELSE '' END AS LOW_PERFORMER,
                           CASE WHEN CAP_ISSUE_TYPE  IS NOT NULL THEN '✓' ELSE '' END AS HAS_CAP,
                           CAP_ISSUE_TYPE,
                           CASE WHEN OVERALL_FAC     IS NOT NULL THEN '✓' ELSE '' END AS CAI_FLAG,
                           (CASE WHEN CAP_ISSUE_TYPE IS NOT NULL THEN 30 ELSE 0 END
                          + CASE WHEN REASON_FOR_LPI IS NOT NULL THEN 25 ELSE 0 END
                          + CASE WHEN TRY_TO_DECIMAL(OVERALL_STARS) < 3.0 THEN 20
                                 WHEN TRY_TO_DECIMAL(OVERALL_STARS) < 3.5 THEN 10 ELSE 0 END
                          + CASE WHEN OVERALL_FAC    IS NOT NULL THEN 15 ELSE 0 END) AS OPPORTUNITY_SCORE
                    FROM BASE
                    WHERE (CASE WHEN CAP_ISSUE_TYPE IS NOT NULL THEN 30 ELSE 0 END
                         + CASE WHEN REASON_FOR_LPI IS NOT NULL THEN 25 ELSE 0 END
                         + CASE WHEN TRY_TO_DECIMAL(OVERALL_STARS) < 3.0 THEN 20
                                WHEN TRY_TO_DECIMAL(OVERALL_STARS) < 3.5 THEN 10 ELSE 0 END
                         + CASE WHEN OVERALL_FAC    IS NOT NULL THEN 15 ELSE 0 END) >= {min_score}
                    {star_clause}
                    ORDER BY OPPORTUNITY_SCORE DESC LIMIT {limit}
                """)
                c1,c2,c3,c4,c5 = st.columns(5)
                c1.metric("Plans Found", len(df))
                c2.metric("With CAP", int((df["HAS_CAP"]=="✓").sum()))
                c3.metric("Low Performers", int((df["LOW_PERFORMER"]=="✓").sum()))
                try:
                    c4.metric("Avg Stars", f"{pd.to_numeric(df['OVERALL_STARS'], errors='coerce').mean():.2f}")
                    c5.metric("Total Enrollment", f"{pd.to_numeric(df['ENROLLMENT'], errors='coerce').sum():,.0f}")
                except: pass
                st.dataframe(df, use_container_width=True, height=420)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — CAP ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown('<div class="section-header">CAP Enforcement Actions</div>', unsafe_allow_html=True)
    cap_view = st.radio("View", ["All CAP Issues","By Parent Organization","By Issue Type","CAP + Stars + Enrollment"], horizontal=True)
    if st.button("📋 Load", key="cap_load", type="primary"):
        with st.spinner("Loading..."):
            try:
                if cap_view == "All CAP Issues":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               PARENT_ORGANIZATION, STATE, MBR_CNT AS ENROLLMENT,
                               CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                               CAP_CONTACT_NAME, CAP_CONTACT_PHONE,
                               OVERALL_STARS, CAP_ISSUE_TYPE, CAP_ISSUE_SUMMARY
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL
                        ORDER BY OVERALL_STARS ASC"""
                elif cap_view == "By Parent Organization":
                    q = f"""{BASE_CTE}
                        SELECT PARENT_ORGANIZATION, COUNT(DISTINCT CONTRACT_ID) AS CONTRACTS,
                               COUNT(CAP_ISSUE_TYPE) AS CAP_ISSUES,
                               SUM(MBR_CNT) AS TOTAL_ENROLLMENT,
                               MIN(TRY_TO_DECIMAL(OVERALL_STARS)) AS LOWEST_STARS
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL
                        GROUP BY PARENT_ORGANIZATION ORDER BY CAP_ISSUES DESC"""
                elif cap_view == "By Issue Type":
                    q = f"""{BASE_CTE}
                        SELECT CAP_ISSUE_TYPE, COUNT(*) AS TOTAL,
                               COUNT(DISTINCT CONTRACT_ID) AS CONTRACTS_AFFECTED,
                               SUM(MBR_CNT) AS MEMBERS_AFFECTED
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL
                        GROUP BY CAP_ISSUE_TYPE ORDER BY TOTAL DESC"""
                else:
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, MBR_CNT AS ENROLLMENT,
                               CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                               CAP_CONTACT_NAME, CAP_CONTACT_PHONE,
                               OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                               CAP_ISSUE_TYPE, CAP_ISSUE_SUMMARY, REASON_FOR_LPI
                        FROM BASE WHERE CAP_ISSUE_TYPE IS NOT NULL
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
    if st.button("⚠️ Load Low Performers", type="primary"):
        with st.spinner("Loading..."):
            try:
                df = run_query(f"""{BASE_CTE}
                    SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                           PARENT_ORGANIZATION, STATE, MBR_CNT AS ENROLLMENT,
                           CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_PHONE, CONTACT_EMAIL,
                           OVERALL_STARS, PART_C_STARS, PART_D_STARS, REASON_FOR_LPI,
                           CASE WHEN CAP_ISSUE_TYPE IS NOT NULL THEN 'YES' ELSE 'NO' END AS HAS_CAP,
                           CAP_ISSUE_TYPE, CAP_CONTACT_NAME, CAP_CONTACT_PHONE,
                           CASE WHEN OVERALL_FAC    IS NOT NULL THEN 'YES' ELSE 'NO' END AS HAS_CAI_FLAG
                    FROM BASE WHERE REASON_FOR_LPI IS NOT NULL
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
    star_view = st.radio("View", ["All Plans","Below 3.0 Stars","Below 3.5 Stars","4.0+ Stars","Part C vs D Gap","Domain Stars","High Performers"], horizontal=True)
    state_filter = st.text_input("Filter by State (optional)", placeholder="e.g. CA, TX, FL")

    if st.button("📊 Load", key="star_load", type="primary"):
        with st.spinner("Loading..."):
            try:
                sc = f"AND STATE = '{state_filter.strip().upper()}'" if state_filter else ""
                if star_view == "All Plans":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, MBR_CNT AS ENROLLMENT,
                               OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                               CASE WHEN REASON_FOR_LPI IS NOT NULL THEN '✓' ELSE '' END AS LOW_PERFORMER,
                               CASE WHEN CAP_ISSUE_TYPE IS NOT NULL THEN '✓' ELSE '' END AS HAS_CAP
                        FROM BASE WHERE 1=1 {sc} ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC"""
                elif star_view == "Below 3.0 Stars":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, MBR_CNT AS ENROLLMENT, OVERALL_STARS, PART_C_STARS, PART_D_STARS,
                               REASON_FOR_LPI, CAP_ISSUE_TYPE
                        FROM BASE WHERE TRY_TO_DECIMAL(OVERALL_STARS) < 3.0 {sc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC"""
                elif star_view == "Below 3.5 Stars":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, MBR_CNT AS ENROLLMENT, OVERALL_STARS, PART_C_STARS, PART_D_STARS
                        FROM BASE WHERE TRY_TO_DECIMAL(OVERALL_STARS) < 3.5 {sc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC"""
                elif star_view == "4.0+ Stars":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               STATE, MBR_CNT AS ENROLLMENT, OVERALL_STARS, PART_C_STARS, PART_D_STARS
                        FROM BASE WHERE TRY_TO_DECIMAL(OVERALL_STARS) >= 4.0 {sc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) DESC"""
                elif star_view == "Part C vs D Gap":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               PART_C_STARS, PART_D_STARS, OVERALL_STARS,
                               ROUND(TRY_TO_DECIMAL(PART_C_STARS)-TRY_TO_DECIMAL(PART_D_STARS),2) AS C_MINUS_D_GAP
                        FROM BASE WHERE 1=1 {sc}
                        ORDER BY ABS(TRY_TO_DECIMAL(PART_C_STARS)-TRY_TO_DECIMAL(PART_D_STARS)) DESC NULLS LAST"""
                elif star_view == "Domain Stars":
                    q = f"SELECT * FROM {DB}.STAR_RATINGS_DOMAIN_STARS ORDER BY 1"
                else:
                    q = f"SELECT * FROM {DB}.STAR_RATINGS_HIGH_PERFORMING_CONTRACTS ORDER BY 1"
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
    measure_view = st.radio("View", ["Full Measure Dataset","Weak Measures (Stars < 3)","2027 Part C Weights","2027 Part D Weights","Measure Crosswalk","Part C Cut Points","Part D Cut Points"], horizontal=True)
    contract_filter = st.text_input("Filter by Contract ID (optional)", placeholder="e.g. H0001")

    if st.button("💊 Load", key="meas_load", type="primary"):
        with st.spinner("Loading..."):
            try:
                cc = f"AND CONTRACT_ID = '{contract_filter.strip().upper()}'" if contract_filter else ""
                if measure_view == "Full Measure Dataset":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME,
                               MBR_CNT AS ENROLLMENT, OVERALL_STARS,
                               C01_DATA,C01_STARS,C01_WEIGHT, C02_DATA,C02_STARS,C02_WEIGHT,
                               C03_DATA,C03_STARS,C03_WEIGHT, C04_DATA,C04_STARS,C04_WEIGHT,
                               C05_DATA,C05_STARS,C05_WEIGHT, C12_DATA,C12_STARS,C12_WEIGHT,
                               C14_DATA,C14_STARS,C14_WEIGHT, C18_DATA,C18_STARS,C18_WEIGHT,
                               D08_DATA,D08_STARS,D08_WEIGHT, D09_DATA,D09_STARS,D09_WEIGHT,
                               D10_DATA,D10_STARS,D10_WEIGHT
                        FROM BASE WHERE 1=1 {cc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC LIMIT 200"""
                elif measure_view == "Weak Measures (Stars < 3)":
                    q = f"""{BASE_CTE}
                        SELECT CONTRACT_ID, ORGANIZATION_MARKETING_NAME AS PLAN_NAME, OVERALL_STARS,
                               CASE WHEN TRY_TO_DECIMAL(C01_STARS)<3 THEN C01_STARS END AS C01_BREAST_CANCER,
                               CASE WHEN TRY_TO_DECIMAL(C02_STARS)<3 THEN C02_STARS END AS C02_COLORECTAL,
                               CASE WHEN TRY_TO_DECIMAL(C03_STARS)<3 THEN C03_STARS END AS C03_FLU,
                               CASE WHEN TRY_TO_DECIMAL(C12_STARS)<3 THEN C12_STARS END AS C12_BLOOD_SUGAR,
                               CASE WHEN TRY_TO_DECIMAL(C14_STARS)<3 THEN C14_STARS END AS C14_BLOOD_PRESSURE,
                               CASE WHEN TRY_TO_DECIMAL(C18_STARS)<3 THEN C18_STARS END AS C18_READMISSIONS,
                               CASE WHEN TRY_TO_DECIMAL(D08_STARS)<3 THEN D08_STARS END AS D08_MED_ADHERENCE_DIAB,
                               CASE WHEN TRY_TO_DECIMAL(D09_STARS)<3 THEN D09_STARS END AS D09_MED_ADHERENCE_HTN,
                               CASE WHEN TRY_TO_DECIMAL(D10_STARS)<3 THEN D10_STARS END AS D10_MED_ADHERENCE_CHOL
                        FROM BASE WHERE 1=1 {cc}
                        ORDER BY TRY_TO_DECIMAL(OVERALL_STARS) ASC LIMIT 200"""
                elif measure_view == "2027 Part C Weights":
                    q = f"SELECT * FROM {DB}.STAR_RATINGS_2027_PART_C_MEASURES ORDER BY PART_C_SUMMARY_AND_MA_PD_OVERALL_WEIGHT DESC"
                elif measure_view == "2027 Part D Weights":
                    q = f"SELECT * FROM {DB}.STAR_RATINGS_2027_PART_D_MEASURES ORDER BY PART_D_SUMMARY_AND_MA_PD_OVERALL_WEIGHT DESC"
                elif measure_view == "Measure Crosswalk":
                    q = f"SELECT * FROM {DB}.STAR_RATINGS_MEASURE_CROSSWALK ORDER BY COLUMN_INDEX"
                elif measure_view == "Part C Cut Points":
                    q = f"SELECT * FROM {DB}.STAR_RATINGS_PART_C_CUT_POINTS"
                else:
                    q = f"SELECT * FROM {DB}.STAR_RATINGS_PART_D_CUT_POINTS"
                df = run_query(q)
                st.success(f"{len(df)} rows")
                st.dataframe(df, use_container_width=True, height=450)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — CONTRACT DIRECTORY
# ══════════════════════════════════════════════════════════════════════════════
with tab6:
    st.markdown('<div class="section-header">MA Contract Directory 2026</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        search = st.text_input("Search plan name or contract number", placeholder="e.g. Humana or H0001")
    with col2:
        state = st.text_input("State (optional)", placeholder="e.g. CA")
    if st.button("🔍 Search", type="primary"):
        with st.spinner("Searching..."):
            try:
                where = []
                if search:
                    where.append(f"(UPPER(ORGANIZATION_MARKETING_NAME) LIKE UPPER('%{search}%') OR UPPER(CONTRACT_NUMBER) LIKE UPPER('%{search}%'))")
                if state:
                    where.append(f"UPPER(LEGAL_ENTITY_STATE_CODE) = UPPER('{state}')")
                where_str = "WHERE " + " AND ".join(where) if where else ""
                df = run_query(f"SELECT * FROM {DB}.MA_CONTRACT_DIRECTORY_2026_04 {where_str} LIMIT 300")
                st.success(f"{len(df)} contracts found")
                st.dataframe(df, use_container_width=True, height=450)
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — AI CHATBOT
# ══════════════════════════════════════════════════════════════════════════════
with tab7:
    st.markdown('<div class="section-header">MA Consulting AI Chatbot</div>', unsafe_allow_html=True)
    st.caption("Powered by mistral-large2 · Ask anything about MA strategy, Star Ratings, CAP, and consulting opportunities")

    SYSTEM_PROMPT = """You are an expert Medicare Advantage consulting analyst for Sadaf,
    a Lead BI Developer building an independent consulting firm targeting small and regional MA plans.

    You have deep knowledge of:
    - CMS Star Ratings (Part C & D measures, weights 1-5, cut points)
    - Risk Adjustment and RAF scores
    - CAP enforcement actions and compliance remediation
    - Low performer identification and improvement strategies
    - Medication adherence measures (D08, D09, D10 each weight 3)
    - Clinical quality: HEDIS, CAHPS, HOS measures
    - 2027 changes: new measures (Care for Older Adults FSA, COB, Poly-ACH); removed (Pain Assessment, Med Rec, MTM CMR)
    - Consulting strategy for small/regional MA plans

    Database has 769 MA contracts with 2026 Star Ratings, 503 CAP enforcement actions,
    4 low performer plans, and enrollment from MA Contract Directory.

    Opportunity scoring: CAP=+30, Low performer=+25, Stars<3.0=+20, Stars 3.0-3.4=+10, CAI flag=+15.

    Be concise, strategic, and actionable. Use bullet points when listing items."""

    # Quick question buttons
    st.write("**Quick questions:**")
    quick_qs = [
        "Which plans should I target first?",
        "What does a CAP issue mean for a plan?",
        "Which 2027 measures have the highest weight?",
        "How do I pitch my services to a low performer?",
        "What changed in 2027 Star Ratings?",
        "How is the opportunity score calculated?",
        "What is a CAI flag?",
        "How does medication adherence affect stars?",
        "What is risk adjustment consulting?",
    ]
    cols = st.columns(3)
    for i, q in enumerate(quick_qs):
        if cols[i % 3].button(q, use_container_width=True, key=f"cq{i}"):
            st.session_state.setdefault("chat_messages", [])
            if not st.session_state.chat_messages or st.session_state.chat_messages[-1]["content"] != q:
                st.session_state.chat_messages.append({"role": "user", "content": q})
                st.session_state.chat_run = True

    st.divider()

    # Init chat history
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    # Display chat history
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input box
    if prompt := st.chat_input("Ask about MA consulting, Star Ratings, CAP issues, measures..."):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.chat_run = True

    # Generate AI response
    if st.session_state.get("chat_run") and st.session_state.chat_messages:
        st.session_state.chat_run = False
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    import json
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    for m in st.session_state.chat_messages[-10:]:
                        messages.append({"role": m["role"], "content": m["content"]})

                    messages_str = json.dumps(messages).replace("\\", "\\\\").replace("'", "\\'")

                    conn = get_connection()
                    cur = conn.cursor()
                    cur.execute(f"""
                        SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{messages_str}') AS ANSWER
                    """)
                    raw = cur.fetchone()[0]
                    try:
                        parsed = json.loads(raw)
                        answer = parsed["choices"][0]["message"]["content"]
                    except Exception:
                        answer = raw
                    st.markdown(answer)
                    st.session_state.chat_messages.append({"role": "assistant", "content": answer})
                except Exception as e:
                    st.error(f"Error: {e}")

    # Clear button
    if st.session_state.get("chat_messages"):
        if st.button("🗑️ Clear conversation", key="clear_chat"):
            st.session_state.chat_messages = []
            st.rerun()
