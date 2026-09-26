"""Target roles match the titles employers actually write, without matching other work."""

from backend.job_quality import ProfileRules
from backend.role_titles import RoleMatcher, alternatives, related_titles


def matcher(*roles, **options):
    return RoleMatcher(list(roles), options.get("related"), options.get("excluded"))


def test_word_order_punctuation_and_level_words_do_not_hide_a_target_role():
    data = matcher("Data Analyst")
    for title in ("Analyst, Data & Insights", "Graduate Analyst - Data", "Junior Data Analyst (Hybrid)",
                  "Senior Data Analyst", "Data Analytics Graduate Programme"):
        assert data.search(title), title
    for title in ("Financial Analyst", "Data Entry Clerk", "Sales Development Representative"):
        assert not data.search(title), title


def test_abbreviations_count_both_ways():
    assert matcher("Machine Learning Engineer").search("ML Engineer")
    assert matcher("ML Engineer").search("Machine Learning Engineer, Search")
    assert matcher("BI Developer").search("Business Intelligence Developer")
    assert matcher("Business Intelligence Analyst").search("BI Analyst")


def test_slash_roles_share_a_head_or_name_two_roles():
    assert set(alternatives("BI/Power BI Developer")) == {("developer", frozenset({"bi"})),
                                                         ("developer", frozenset({"power", "bi"}))}
    assert set(alternatives("Data Engineer/Data Scientist")) == {("engineer", frozenset({"data"})),
                                                                ("scientist", frozenset({"data"}))}
    reporting = matcher("Reporting/Insights Analyst")
    assert reporting.search("Customer Insights Analyst")
    assert reporting.search("Reporting Analyst - Finance")
    assert not reporting.search("Security Analyst")


def test_developer_and_engineer_are_one_job_and_hyphens_do_not_matter():
    assert matcher("Software Developer").search("Software Engineer, New Grad")
    assert matcher("Full-Stack Developer").search("Full Stack Software Engineer")


def test_related_titles_widen_the_net_only_for_the_same_work():
    related = related_titles(["Data Analyst"])
    assert "insights analyst" in related and "mi analyst" in related
    widened = matcher("Data Analyst", related=related)
    assert widened.search("MI Analyst")
    assert widened.search("Power BI Analyst") or widened.search("Reporting Analyst")
    assert not widened.search("Mechanical Engineer")


def test_excluded_titles_win():
    assert not matcher("Software Engineer", excluded=["sales"]).search("Sales Engineer")
    assert matcher("Software Engineer", excluded=["sales"]).search("Software Engineer")


def test_profile_rules_read_related_and_excluded_titles(tmp_path):
    rules = ProfileRules({"target_roles": {"primary": ["Data Analyst"], "related_titles": ["Insights Associate"],
                                           "excluded_titles": ["intern"]}})
    assert rules.roles.search("Insights Associate")
    assert rules.roles.search("MI Analyst")  # built-in related title for data analysts
    assert not rules.roles.search("Data Analyst Intern")
    narrow = ProfileRules({"target_roles": {"primary": ["Data Analyst"], "expand_related": False}})
    assert not narrow.roles.search("MI Analyst")
    (tmp_path / "data/config").mkdir(parents=True)
    (tmp_path / "data/config/profile.yml").write_text("target_roles: {primary: [Data Analyst]}\n", encoding="utf-8")
    (tmp_path / "data/config/portals.yml").write_text("filters: {exclude_title_words: [contract]}\n", encoding="utf-8")
    assert not ProfileRules.of(tmp_path).roles.search("Data Analyst - Contract")
    assert ProfileRules.of(tmp_path).roles.search("Data Analyst")
