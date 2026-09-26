"""A merged intake draft, as the AI would return it for example_profile/About Me-1.docx.

Used to build a second profile without calling any AI. Facts and wording come from
that document; block ids are illustrative.
"""

import copy

SECTION = {
    "contact": {"full_name": "Srikanth Nadesharam", "preferred_name": "", "email": "srikanth.n@example.com",
                "phone": "+353 87 123 4567", "linkedin": "", "github": "", "portfolio_url": "",
                "city": "Cork", "country": "Ireland", "languages": [], "refs": ["P004", "P006"]},
    "authorization": {"work_country": "Ireland", "status": "Stamp 1G", "valid_until": "DEC2027",
                      "conditions": "40 hours", "needs_sponsorship_later": "unknown", "citizenship": "", "refs": ["P460"]},
    "targets": {"roles": ["Data Analyst", "Data Scientist", "Machine Learning Engineer"], "countries": ["Ireland"],
                "cities": ["Cork"], "arrangements": ["On-site", "Hybrid"], "seniority": "graduate",
                "salary": "open to a market-aligned salary", "availability": "immediately", "refs": ["P006", "P055"]},
    "education": [
        {"institution": "Jawaharlal Nehru Technological University, Hyderabad", "degree": "Bachelor of Technology",
         "field": "Computer Science and Engineering", "location": "Hyderabad, India", "start": "2019", "end": "2023",
         "grade": "CGPA 7.41, First Class", "coursework": ["Data Structures", "Database Management Systems", "Operating Systems"],
         "facts": ["Completed all four years without a single backlog"], "refs": ["P011", "P012"]},
        {"institution": "Munster Technological University", "degree": "Master of Science", "field": "Data Science and Analytics",
         "location": "Cork, Ireland", "start": "September 2024", "end": "January 2026", "grade": "",
         "coursework": ["Python for Data Science", "R Programming", "Statistics", "Machine Learning", "Time Series Forecasting",
                        "Data Visualisation", "Feature Engineering", "Model Evaluation", "Tableau", "Power BI"],
         "facts": [], "refs": ["P039", "P040"]},
    ],
    "experience": [
        {"employer": "JRB Infotech", "title": "Associate Data Analyst", "location": "Hyderabad, India",
         "start": "June 2022", "end": "September 2024", "employment_type": "part-time, then full-time",
         "client_or_domain": "a leading banking client",
         "bullets": [
             "Designed and executed data validation workflows across more than 50,000 banking records",
             "Built SQL queries joining customer, account and transaction tables to answer business questions",
             "Created interactive Power BI dashboards on a star schema to monitor banking KPIs for stakeholders",
             "Automated recurring MIS reports with SQL and Python scripting",
         ],
         "metrics": ["95 percent improvement in data quality", "reduced data entry errors by about 12 percent",
                     "roughly a 10 percent reduction in operational expenses"],
         "tools": ["SQL", "Python", "Pandas", "Power BI", "Power Query", "Excel", "SQL Server"],
         "refs": ["P060", "P061", "P062"]},
    ],
    "projects": [
        {"name": "1. AMCS Waste Operations Analytics & Forecasting", "kind": "academic", "period": "MSc", "organisation": "",
         "ownership": "", "summary": "An end-to-end analytics pipeline for waste collection operations",
         "facts": ["Built a LightGBM forecasting model benchmarked against a seasonal-naive baseline on 17,000+ records",
                   "Designed dual anomaly detection combining a rolling z-score with Isolation Forest",
                   "Delivered a Streamlit dashboard backed by a 12-test unit suite that catches data-leakage bugs"],
         "metrics": ["R² of 0.916 and 20.1kg MAE, a 31% error reduction over the baseline"],
         "tools": ["Python", "LightGBM", "Isolation Forest", "Streamlit"], "refs": ["P074", "P075", "P076"]},
        {"name": "2. Dublin Hourly Cycling Demand Forecasting", "kind": "academic", "period": "MSc", "organisation": "",
         "ownership": "", "summary": "Hourly cycling demand forecasts for six Dublin locations",
         "facts": ["Integrated 157,824 hourly observations by matching cycle counters to SCATS traffic sensors and weather",
                   "Engineered location-safe lag features and held out a six-month test period",
                   "Benchmarked seasonal, Ridge, Random Forest and gradient boosting models on validation performance"],
         "metrics": ["MAE 7.28, RMSE 12.24, R² 0.968 and WAPE 16.8% on the untouched test set"],
         "tools": ["Python", "scikit-learn", "Streamlit"], "refs": ["P079", "P080"]},
        {"name": "Local LLM & RAG System", "kind": "self-directed", "period": "", "organisation": "", "ownership": "",
         "summary": "", "facts": ["Built a local retrieval-augmented generation system"], "metrics": [],
         "tools": ["Python", "Ollama", "FAISS"], "refs": ["P250"]},
    ],
    "skills": [
        {"name": "Programming and databases", "skills": ["Python", "Pandas", "NumPy", "Scikit-learn", "SQL", "R", "MySQL", "SQL Server", "PostgreSQL"],
         "level": "strong", "refs": ["P047"]},
        {"name": "Machine learning", "skills": ["XGBoost", "LightGBM", "Random Forest", "K-Means", "ARIMA", "Exponential smoothing"],
         "level": "used", "refs": ["P048"]},
        {"name": "Business intelligence", "skills": ["Power BI", "Tableau", "Excel", "Matplotlib"], "level": "used", "refs": ["P049"]},
        {"name": "Cloud", "skills": ["AWS", "SageMaker", "S3", "Vertex AI", "Azure ML"], "level": "familiar", "refs": ["P047"]},
    ],
    "certifications": [{"name": "Data Science Essentials with Python", "issuer": "Cisco Networking Academy",
                        "date": "Issued June 11, 2026", "details": ["Data Frame operations, merges and cleaning"], "refs": ["P311", "P312"]}],
    "statements": [
        {"topic": "strength", "text": "I have strong analytical and logical thinking and a good mathematical foundation.", "refs": ["P052"]},
        {"topic": "weakness", "text": "I now define the baseline and evaluation method before trying complex models.", "refs": ["P053"]},
        {"topic": "school results", "text": "Completed schooling with a 9.2 GPA out of 10 and scored 950 out of 1000 in Intermediate (MPC).", "refs": ["P009"]},
        {"topic": "career goal", "text": "To build a career as a Data Scientist, Machine Learning Engineer, AI Engineer or Data Analyst.", "refs": ["P056"]},
    ],
    "interview_answers": [
        {"question": "Tell me about yourself", "answer": "I recently completed an MSc in Data Science and Analytics at Munster Technological University.", "refs": ["P315"]},
        {"question": "What is your work authorisation?", "answer": "I currently hold Stamp 1G permission, which allows me to work 40 hours. It is valid until DEC2027.", "refs": ["P460"]},
    ],
    "narrative_only": ["P005"],
    "questions": ["JRB Infotech overlapped with your bachelor's degree: were you part-time until August 2023?"],
}


def section():
    return copy.deepcopy(SECTION)


def blocks():
    """Blocks matching the refs above, so coverage can be computed."""
    ids = sorted({r for part in _walk(SECTION) for r in part} | {"P005", "P999"})
    return [{"id": i, "kind": "paragraph", "text": f"Block {i} text with 17,000 records", "source": "About Me-1.docx"} for i in ids]


def _walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "refs":
                yield item
            else:
                yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
