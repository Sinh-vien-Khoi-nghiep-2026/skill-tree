"""Public surface of the career-counseling example."""

from .analytics import (
    CareerRecommendation,
    SkillGrowth,
    average_skill_level,
    recommend_careers,
    skill_growth,
    strongest_skills,
)
from .models import Career, Interest, Skill, Student
from .serialization import (
    CAREER_TYPE_ID,
    INTEREST_TYPE_ID,
    SKILL_TYPE_ID,
    STUDENT_TYPE_ID,
    create_career_registry,
    register_career_types,
)

__all__ = [
    "CAREER_TYPE_ID",
    "INTEREST_TYPE_ID",
    "SKILL_TYPE_ID",
    "STUDENT_TYPE_ID",
    "Career",
    "CareerRecommendation",
    "Interest",
    "Skill",
    "SkillGrowth",
    "Student",
    "average_skill_level",
    "create_career_registry",
    "recommend_careers",
    "register_career_types",
    "skill_growth",
    "strongest_skills",
]
