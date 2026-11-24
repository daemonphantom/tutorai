# Web app and MongoDB

MongoDB stores user accounts and chats. Run the commands from the repository root. Docker Compose and the app read the credentials from `.env` (template: `.env.example`).

## Start MongoDB
```bash
docker compose --env-file .env -f src/interface/docker-compose.yml up -d
```

## Start TutorAI
Neo4j and Ollama must be running.
```bash
source .venv/bin/activate
python -m src.interface.app
```

## Addresses
- TutorAI: http://localhost:5001
- MongoDB web UI (mongo-express): http://localhost:8081

## Stop MongoDB
```bash
docker compose --env-file .env -f src/interface/docker-compose.yml down
```
