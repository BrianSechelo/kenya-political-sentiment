from dataclasses import dataclass
from typing import Literal

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


MODEL_VERSION = "vader-3.3.2"
SentimentLabel = Literal["positive", "negative", "neutral"]


@dataclass(frozen=True)
class SentimentPrediction:
    sentiment_label: SentimentLabel
    confidence_score: float
    compound_score: float
    model_version: str = MODEL_VERSION


_analyzer = SentimentIntensityAnalyzer()


def analyze_sentiment(text: str) -> SentimentPrediction:
    cleaned_text = text.strip()

    if not cleaned_text:
        raise ValueError("Text must not be empty")

    scores = _analyzer.polarity_scores(cleaned_text)
    compound_score = float(scores["compound"])

    if compound_score >= 0.05:
        sentiment_label: SentimentLabel = "positive"
        confidence_score = float(scores["pos"])
    elif compound_score <= -0.05:
        sentiment_label = "negative"
        confidence_score = float(scores["neg"])
    else:
        sentiment_label = "neutral"
        confidence_score = float(scores["neu"])

    return SentimentPrediction(
        sentiment_label=sentiment_label,
        confidence_score=round(confidence_score, 4),
        compound_score=round(compound_score, 4),
    )