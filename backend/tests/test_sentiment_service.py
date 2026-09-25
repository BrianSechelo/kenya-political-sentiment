import pytest

from app.services.sentiment_service import analyze_sentiment


def test_positive_sentiment():
    result = analyze_sentiment("This leader is doing a great job")

    assert result.sentiment_label == "positive"
    assert result.compound_score >= 0.05
    assert 0.0 <= result.confidence_score <= 1.0
    assert result.model_version == "vader-3.3.2"


def test_negative_sentiment():
    result = analyze_sentiment("This leader is corrupt and terrible")

    assert result.sentiment_label == "negative"
    assert result.compound_score <= -0.05


def test_neutral_sentiment():
    result = analyze_sentiment("The meeting begins at three o'clock")

    assert result.sentiment_label == "neutral"
    assert -0.05 < result.compound_score < 0.05


def test_empty_text_is_rejected():
    with pytest.raises(ValueError, match="Text must not be empty"):
        analyze_sentiment("   ")