from fastapi import FastAPI

app = FastAPI(
    title="Kenya Political Sentiment API",
    version="0.1.0",
)


@app.get("/")
def root():
    return {
        "message": "Kenya Political Sentiment API"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }