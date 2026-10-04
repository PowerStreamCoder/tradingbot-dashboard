import json
import os
from unittest.mock import MagicMock, patch

from stockpicker.core import call_llm_rank, heuristic_rank
from stockpicker.runner import detect_active_sources


def test_detect_active_sources_gemini():
    """Verify detect_active_sources detects GEMINI_API_KEY and not openai."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy_gemini_key"}, clear=True):
        sources = detect_active_sources()
        assert "gemini" in sources
        assert "openai" not in sources
        assert "sec_edgar" in sources
        assert "yahoo_finance" in sources


def test_call_llm_rank_fallback_without_api_key():
    """Verify call_llm_rank falls back cleanly to heuristic_rank when GEMINI_API_KEY is unset."""
    sample_records = [
        {
            "headline": "Tech giant announces breakthrough AI chip",
            "summary": "Massive increase in data center GPU performance",
            "engagement": 500,
            "tickers": "NVDA",
        }
    ]
    with patch.dict(os.environ, {}, clear=True):
        # Ensure GEMINI_API_KEY is not set
        result = call_llm_rank(sample_records)
        assert len(result) == 1
        assert "explosiveness" in result[0]
        assert result[0]["industry"] in ["AI/Cloud Infrastructure", "Semiconductors/Foundry"]


def test_call_llm_rank_gemini_success():
    """Verify call_llm_rank properly calls Gemini endpoint and parses response."""
    sample_records = [
        {
            "headline": "Energy company signs major supply deal",
            "summary": "Oil tanker shipping demand surges",
            "engagement": 1200,
            "tickers": "FRO",
        }
    ]

    mock_gemini_ranked_output = [
        {
            "headline": "Energy company signs major supply deal",
            "explosiveness": 8.5,
            "industry": "Energy/Tanker Shipping",
            "direct_ticker": "FRO",
            "thesis": "High shipping demand",
            "impacted_us_tickers": ["FRO", "STNG"],
            "rationale_short": "Surge in contract volume",
        }
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps(mock_gemini_ranked_output)}
                    ]
                }
            }
        ]
    }

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-2.5-flash"}):
        with patch("requests.post", return_value=mock_response) as mock_post:
            result = call_llm_rank(sample_records)

            assert len(result) == 1
            assert result[0]["explosiveness"] == 8.5
            assert result[0]["industry"] == "Energy/Tanker Shipping"
            assert result[0]["direct_ticker"] == "FRO"

            # Check that requests.post was called with the Gemini endpoint
            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            assert "generativelanguage.googleapis.com" in call_url
            assert "gemini-2.5-flash" in call_url
            assert "key=test-key" in call_url


def test_call_llm_rank_gemini_error_fallback():
    """Verify call_llm_rank falls back to heuristic_rank when Gemini API throws an error."""
    sample_records = [
        {
            "headline": "Cybersecurity firm reports quarterly profit",
            "summary": "Ransomware protection demand grew",
            "engagement": 100,
            "tickers": "CRWD",
        }
    ]

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("requests.post", side_effect=Exception("API timeout")):
            result = call_llm_rank(sample_records)
            assert len(result) == 1
            assert "explosiveness" in result[0]
            assert result[0]["industry"] == "Cybersecurity"
