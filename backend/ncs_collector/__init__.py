"""Deterministic certification and NCS gap analysis.

This package is the authoritative rules layer. It never calls an LLM or a
retrieval service; Bedrock Knowledge Base results are evidence only.
"""

from .certifications import CertificationNormalizer
from .gap_analyzer import analyze_gap
from .models import ApplicantSpecInput, StructuredGapAnalysis
from .trade_requirements import LocalRuleRepository, RuleRepository

__all__ = [
    "ApplicantSpecInput",
    "CertificationNormalizer",
    "LocalRuleRepository",
    "RuleRepository",
    "StructuredGapAnalysis",
    "analyze_gap",
]
