# OneLife dev shortcuts. Windows without `make`? Use the docker compose
# commands shown in each recipe directly (see AUTHORING.md / README.md).

.PHONY: up down reset seed lint logs

up:          ## build + start the stack
	docker compose up --build -d

down:        ## stop the stack (keeps the DB)
	docker compose down

reset:       ## wipe the DB volume and start fresh
	docker compose down -v && docker compose up --build -d

seed:        ## validate + load content/ into Postgres
	docker compose run --rm api python -m app.seed

lint:        ## validate content/ only (no DB) — schema, refs, spine reachability
	docker compose run --rm --no-deps api python -m app.seed --lint

logs:        ## follow API logs
	docker compose logs -f api
