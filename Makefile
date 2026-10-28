.PHONY: up down logs test eval seed lint dev

up:            ## собрать и запустить на http://localhost:8080
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app

seed:          ## наполнить демо-метрики: прогнать тестовые сценарии внутри контейнера
	docker compose exec app python -m eval.run_eval

eval: seed

test:          ## юнит-тесты локально
	python -m pytest -q tests

lint:
	ruff check app eval tests run.py

dev:           ## локальный запуск без Docker (нужен Python 3.11+)
	pip install -r requirements-dev.txt && uvicorn app.main:app --reload --port 8080
