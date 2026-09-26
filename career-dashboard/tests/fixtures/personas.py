"""Two onboarding personas for the end-to-end pipeline test.

Both share the workspace's resume contract and evidence registry (the app is
single-user); a persona differs in what the agents are told during onboarding,
which companies the search returns, and what the tailor proposes per company.
"""

STRONG_JD = (
    "Required Python, SQL, Apache Kafka streaming pipelines, Airflow orchestration on AWS Glue and S3, "
    "data validation and quality checks. Responsibilities include ETL, lakehouse data modeling and "
    "clinical device telemetry dashboards. PyTorch a plus."
)


def posting(company, number, title="Data Engineer"):
    slug = company.lower().replace(" ", "")
    return {
        "company": company,
        "title": title,
        "location": "Austin, TX",
        "url": f"https://jobs.lever.co/{slug}/R{number}",
        "requisition_id": f"R{number}",
        "description": STRONG_JD,
        "company_sources": [
            {"title": "Register", "url": f"https://{slug}.example/register", "accessed_at": "2026-09-17"}
        ],
        "legal_presence": "Registered legal entity and trading presence",
        "red_flags": [],
        "size_category": "mid",
        "employee_min": 500,
        "employee_max": 1000,
        "sponsorship_state": "unknown",
        "sponsorship_evidence": [],
        "applicant_count": None,
        "competition_signals": {"posted_within_72h": True, "limited_syndication": False, "niche_match": True},
    }


PERSONAS = {
    "fresher": {
        "knowledge": [
            {"kind": "education", "title": "MS coursework in data systems",
             "summary": "Graduate courses in databases, streaming systems and machine learning."},
            {"kind": "project", "title": "Campus meter pipeline",
             "summary": "Course project moving meter readings through Kafka into PostgreSQL with SQL checks."},
            {"kind": "skill", "title": "Python", "summary": "Used daily in coursework and projects."},
        ],
        "companies": ["Brightbyte", "Civicdata", "Prairie Analytics", "Riverbank Labs", "Storefront Metrics"],
        "predicted_first": {
            "title": "Campus Energy Streaming Prototype",
            "context": "Apache Kafka, Python, PostgreSQL",
            "bullets": [
                "Prototyped a Kafka consumer that aggregates campus meter readings into PostgreSQL summary tables",
                "Added SQL validation queries comparing event counts against source totals",
            ],
        },
        "predicted_second": {
            "title": "Stream Quality Monitor",
            "context": "Python, Apache Kafka",
            "bullets": [
                "Prototyped a monitor that flags stalled Kafka topics from consumer offsets",
                "Logged lag snapshots to PostgreSQL for later inspection",
            ],
        },
        "predicted_skill": "Apache Flink",
    },
    "experienced": {
        "knowledge": [
            {"kind": "experience", "title": "Data platform internship",
             "summary": "Internship work on SQL Server and Azure pipelines, Python automation and validation queries."},
            {"kind": "skill", "title": "Apache Airflow", "summary": "Orchestrated daily ETL during the internship."},
            {"kind": "fact", "title": "Prefers data platform roles",
             "summary": "Lean toward pipeline-heavy data engineering over pure analytics."},
        ],
        "companies": ["Northwind Data", "Helio Systems", "Cascade Cloud", "Beacon Metrics", "Lakeside Logic"],
        "predicted_first": {
            "title": "Pipeline Lag Monitoring Dashboard",
            "context": "Python, Apache Kafka, Grafana",
            "bullets": [
                "Prototyped a consumer-lag exporter that publishes Kafka offsets to a Grafana dashboard",
                "Added SQL checks comparing pipeline throughput across staging and production topics",
            ],
        },
        "predicted_second": {
            "title": "ETL Replay Harness",
            "context": "Python, Airflow, AWS S3",
            "bullets": [
                "Prototyped a harness that replays archived S3 events through an Airflow DAG",
                "Compared rerun outputs against production tables with SQL diffs",
            ],
        },
        "predicted_skill": "Apache Spark",
    },
}

# Real registry projects rotated through the supporting slot (signatures stay unique).
SUPPORTING = ["PROJ-P04-NEWS-RAG", "PROJ-P02-MIGA", "PROJ-P03-DUALFIT", "PROJ-P01-IOT"]
SIGNATURE = "PROJ-P05-RESUME"


def tailoring_for(service, persona, index):
    """The 60/40 result for one job: job 0 leads with its verified signature
    project, the rest lead with a predicted one; skills mix verified + predicted."""
    from backend.ai.agents import schemas

    registry = {p["id"]: p for p in service.w.evidence()["projects"]}
    spec = PERSONAS[persona]

    def verified_project(pid):
        content = registry[pid]["resume_content"]
        return schemas.TailoredProject(
            title=content["title"], context=content["context"],
            bullets=list(content["bullets"]), origin="verified", evidence_id=pid,
        )

    def predicted_project(details):
        return schemas.TailoredProject(origin="predicted", **details)

    if index == 0:
        projects = [verified_project(SIGNATURE), predicted_project(spec["predicted_second"])]
    else:
        projects = [predicted_project(spec["predicted_first"]), verified_project(SUPPORTING[index - 1])]
    skills = [
        schemas.TailoredSkill(name="Python", origin="verified"),
        schemas.TailoredSkill(name="Apache Kafka", origin="verified"),
        schemas.TailoredSkill(name=spec["predicted_skill"], origin="predicted"),
    ]
    return schemas.TailoringResult(
        projects=projects, skills=skills,
        rationale="Kafka and Python match the posting; the proposed project mirrors its pipelines.",
    )
