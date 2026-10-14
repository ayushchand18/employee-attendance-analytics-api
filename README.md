# Employee Attendance & Analytics API

A FastAPI application for employee management, attendance tracking, attendance regularization, and analytics using MongoDB.

## Prerequisites

- Python 3.11+
- MongoDB 6.0+

## Setup

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Configure your environment variables in `.env`:

   ```env
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=attendance_db
   ```

   Use your own MongoDB connection URI if you're using MongoDB Atlas.

4. (Optional) Load the provided sample data:

   ```bash
   python sample_seed.py
   ```

5. Start the API:

   ```bash
   uvicorn app.main:app --port 8000
   ```

## API Documentation

Once the server is running, open:

- Swagger UI: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Main Features

- Employee creation and paginated listing
- Attendance punch-in and punch-out
- Attendance filtering and regularization with audit history
- Employee monthly analytics
- Department summaries, late leaderboard, and daily trends
- MongoDB query explain endpoint

## Submission Notes

- Keep `.env`, credentials, and other secrets out of Git.
- Include `app/main.py`, `requirements.txt`, `REVIEW.md`, and `DECISIONS.md` in the repository.
- Do not include a Dockerfile.
