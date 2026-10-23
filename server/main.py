import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from db import close_db, init_db
from routes import router
from state import BACKGROUND_TASKS

app = FastAPI()

origins = [o.strip() for o in CORS_ORIGINS.split(",")]

app.include_router(router)


@app.on_event("startup")
async def startup():
    await init_db()


@app.on_event("shutdown")
async def shutdown():
    tasks = list(BACKGROUND_TASKS)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await close_db()


# Wrap the complete app so unhandled errors also receive CORS headers.
app = CORSMiddleware(
    app=app,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)
