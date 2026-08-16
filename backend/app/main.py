from fastapi import FastAPI
from app.routers import politicians, politician_aliases

app = FastAPI(
    title="Kenya Political Sentiment API",
    version="0.1.0",
)
app.include_router(politicians.router)
app.include_router(politician_aliases.router)


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