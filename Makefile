.PHONY: dev-up dev-down logs migrate

dev-up:
	docker compose up -d --build

dev-down:
	docker compose down

logs:
	docker compose logs -f --tail=200

migrate:
	docker compose run --rm bot python scripts/migrate_db.py
