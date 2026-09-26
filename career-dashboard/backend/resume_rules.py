"""Canonical resume skill parsing and capitalization rules.

One place decides how a skill is written on the resume. `resume_match.ALIASES`
(which spellings count as a match) and `resume_layout.SIGNALS` (which terms
group together for ranking) answer different questions and stay separate; this
map only answers "how is it spelled when printed".
"""

from __future__ import annotations

import re

CANONICAL = {
    # Business intelligence
    "power bi": "Power BI", "powerbi": "Power BI", "power-bi": "Power BI",
    "power bi service": "Power BI Service", "power query": "Power Query",
    "power query/m": "Power Query/M", "power automate": "Power Automate",
    "dax": "DAX", "tableau": "Tableau", "microstrategy": "MicroStrategy",
    "excel": "Excel", "sharepoint": "SharePoint", "looker": "Looker",
    "qlik": "Qlik", "ssrs": "SSRS", "ssis": "SSIS", "kpi": "KPI", "kpis": "KPIs",
    "bi": "BI", "brd": "BRD", "uat": "UAT",
    # Data platforms
    "sql": "SQL", "t-sql": "T-SQL", "pl/sql": "PL/SQL", "nosql": "NoSQL",
    "mysql": "MySQL", "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
    "mongodb": "MongoDB", "snowflake": "Snowflake", "redshift": "Redshift",
    "bigquery": "BigQuery", "databricks": "Databricks", "dataiku": "Dataiku",
    "nexla": "Nexla", "etl": "ETL", "elt": "ELT", "dbt": "dbt",
    "aws": "AWS", "s3": "S3", "azure": "Azure", "gcp": "GCP",
    "spark": "Spark", "pyspark": "PySpark", "hadoop": "Hadoop", "airflow": "Airflow",
    # Languages and libraries
    "python": "Python", "r": "R", "java": "Java", "javascript": "JavaScript",
    "typescript": "TypeScript", "pandas": "pandas", "numpy": "NumPy",
    "scikit-learn": "scikit-learn", "sklearn": "scikit-learn",
    "scipy": "SciPy", "matplotlib": "Matplotlib", "seaborn": "seaborn",
    "lightgbm": "LightGBM", "xgboost": "XGBoost", "tensorflow": "TensorFlow",
    "pytorch": "PyTorch", "statsmodels": "statsmodels",
    # Other common technical and engineering terms
    "c#": "C#", "c++": "C++", "c": "C", "kafka": "Apache Kafka", "apache kafka": "Apache Kafka",
    "flink": "Apache Flink", "apache flink": "Apache Flink", "flink sql": "Flink SQL",
    "clickhouse": "ClickHouse", "grafana": "Grafana", "apache airflow": "Apache Airflow",
    "aws glue": "AWS Glue", "glue": "AWS Glue", "lambda": "AWS Lambda", "aws lambda": "AWS Lambda",
    "emr": "Amazon EMR", "amazon emr": "Amazon EMR", "amazon s3": "Amazon S3", "ec2": "Amazon EC2",
    "rds": "Amazon RDS", "delta lake": "Delta Lake", "sql server": "SQL Server", "azure sql": "Azure SQL",
    "langchain": "LangChain", "faiss": "FAISS", "ollama": "Ollama", "llama 3.2": "Llama 3.2",
    "rag": "RAG", "streamlit": "Streamlit", "flask": "Flask", "lstm": "LSTM", "yolo": "YOLO",
    "yolo11n-pose": "YOLO11n-Pose", "oc-sort": "OC-SORT", "res-unet": "Res-UNet",
    "labview": "LabVIEW", "teststand": "NI TestStand", "ni teststand": "NI TestStand", "mavis": "MAVIS",
    "wpf": "WPF", ".net": ".NET", "embedded c": "Embedded C", "vivado": "Xilinx Vivado",
    "xilinx vivado": "Xilinx Vivado", "vitis": "Vitis", "microblaze": "MicroBlaze", "axi": "AXI",
    "verilog": "Verilog", "vhdl": "VHDL", "proteus": "Proteus", "mplab": "MPLAB IDE", "hfss": "Ansys HFSS",
    "ansys hfss": "Ansys HFSS", "git": "Git", "bitbucket": "Bitbucket", "jira": "Jira", "docker": "Docker",
    "linux": "Linux", "v&v": "V&V", "computer vision": "computer vision",
    # Statistics and modelling
    "ml": "ML", "machine learning": "machine learning", "ai": "AI",
    "nlp": "NLP", "pca": "PCA", "smote": "SMOTE", "shap": "SHAP",
    "dbscan": "DBSCAN", "knn": "KNN", "svm": "SVM", "arima": "ARIMA",
    "tf-idf": "TF-IDF", "tfidf": "TF-IDF", "eda": "EDA", "a/b testing": "A/B testing",
    # Generative AI
    "genai": "GenAI", "generative ai": "Generative AI", "llm": "LLM", "llms": "LLMs",
    "rag": "RAG", "langchain": "LangChain", "langgraph": "LangGraph",
    "faiss": "FAISS", "chromadb": "ChromaDB", "openai": "OpenAI",
    "hugging face": "Hugging Face", "huggingface": "Hugging Face",
    "api": "API", "apis": "APIs", "json": "JSON", "csv": "CSV", "rest": "REST",
    # Tooling
    "git": "Git", "github": "GitHub", "gitlab": "GitLab", "docker": "Docker",
    "kubernetes": "Kubernetes", "linux": "Linux", "jira": "Jira",
    "confluence": "Confluence", "vs code": "VS Code",
    # Certifications
    "pl-300": "PL-300", "dp-600": "DP-600", "az-900": "AZ-900",
}

# Longest first so "power bi service" is not half-replaced by "power bi".
_ORDERED = sorted(CANONICAL.items(), key=lambda item: -len(item[0]))
_PATTERNS = [
    (re.compile(r"(?<!\w)" + re.escape(raw) + r"(?!\w)", re.I), display)
    for raw, display in _ORDERED
]


def canonicalize_skill(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" ;\n\t")
    direct = CANONICAL.get(value.casefold())
    if direct:
        return direct
    for pattern, display in _PATTERNS:
        value = pattern.sub(display, value)
    return value


def parse_skills(value: str) -> list[str]:
    """Semicolons/newlines separate skills; when there are none, commas do."""
    result, seen = [], set()
    pattern = r"[;\n]+" if re.search(r"[;\n]", value) else r","
    for part in re.split(pattern, value):
        skill = canonicalize_skill(part)
        key = skill.casefold()
        if skill and key not in seen:
            result.append(skill)
            seen.add(key)
    return result


def canonicalize_skill_list(value: str) -> str:
    separator = "; " if re.search(r"[;\n]", value) else ", "
    return separator.join(parse_skills(value))
