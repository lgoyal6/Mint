import importlib.util
from typing import get_args

import mint
import mint.backend
from mint.backend.classification.classifier import _normalize_np_trigger
from mint.backend.models import NpTrigger


CURRENT_NP_TRIGGERS = {
    "direct_competitor",
    "bigtech_or_large_corp",
    "student_or_academic_role",
    "academic_institution",
    "government_public_sector",
    "npo_ngo",
    "large_corp_10k_plus",
    "pre_revenue",
    "sparse_data",
    "low_confidence",
    "no_product_fit",
    "llm_unavailable",
    "parse_error",
    "non_buyer_role",
    "pre_product",
    "not_target",
    "classification_failed",
    "does_not_meet",
}


def test_dead_duplicate_api_factory_is_removed() -> None:
    assert importlib.util.find_spec("mint.backend.api") is None


def test_company_resolution_legacy_model_is_not_exported() -> None:
    assert not hasattr(mint, "CompanyResolution")
    assert not hasattr(mint.backend, "CompanyResolution")


def test_np_trigger_literal_matches_current_write_values() -> None:
    assert set(get_args(NpTrigger)) == CURRENT_NP_TRIGGERS
    for value in CURRENT_NP_TRIGGERS:
        assert _normalize_np_trigger(value, "NP") == value


def test_deprecated_np_triggers_normalize_to_does_not_meet() -> None:
    deprecated = {
        "hard_gate",
        "spend_below_5k",
        "not_a_buyer",
        "weak_affiliation",
        "low_gpu_need",
        "insufficient_evidence",
    }
    for value in deprecated:
        assert _normalize_np_trigger(value, "NP") == "does_not_meet"
