# Kiddybank

A self-hosted banking simulation for kids aged 5–8. It teaches how a bank works — balances, pocket money, interest, fixed-term deposits, saving goals — using play money that the parents control. No real money is involved and nothing connects to a real bank.

The UI is in German. All text goes through a small i18n layer, so other languages can be added.

## Features

- **Giro account** per child, with a statement that explains each booking in one kid-sized sentence
- **Pocket money** as recurring rules set by the parent
- **Interest** with parent-configurable rates, accrued daily and paid out weekly
- **Festgeld** (fixed-term deposits): the child locks money for a term at a higher rate and collects it at maturity
- **Sparziele** (saving goals) with a photo and a progress bar
- **Weekly recap** card on the child's home screen
- Parent area to manage kids, rates, products and bookings
- Profile picker with a 4-digit PIN keypad, big buttons, no typing
- Installable as a PWA; visual feedback only, **no sound**

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run uvicorn app.main:app --reload
```

Open <http://localhost:8000>. The first visit redirects to `/setup`, where you create the parent account. The parent then adds the children.

Configuration (environment variables):

| Variable | Default | Purpose |
|---|---|---|
| `KIDDYBANK_DB` | `kiddybank.db` | SQLite database file |
| `KIDDYBANK_SECRET` | generated into `.session_secret` | Session cookie signing key |

## Development

```sh
uv run pytest                                                        # tests
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify   # rebuild CSS after touching templates
```

`bin/tailwindcss` is the [Tailwind v4 standalone binary](https://github.com/tailwindlabs/tailwindcss/releases); download it into `bin/`. The built `app/static/app.css` is committed, so you only need the binary when you change templates or `app/tailwind.css`.

Stack: FastAPI, SQLModel, SQLite, server-rendered Jinja2, HTMX, Tailwind. No JavaScript build step.

How it fits together:

- `app/main.py` routes and form handling
- `app/ledger.py` all money logic (integer cents, basis-point rates, every booking through `post()`)
- `app/models.py` tables and engine
- `app/i18n.py` UI strings
- `docs/` design specs and the feature backlog

[`CLAUDE.md`](CLAUDE.md) documents the ledger rules and product constraints in detail; read it before touching money code.

## Things to know

- Interest and pocket money are computed lazily on each request. There is no scheduler or background job.
- There is no migration tool. Schema changes need a manual migration or a fresh database.
- The PIN protects against siblings, not attackers. It is designed for a trusted family setting, not for real financial data.
- Service workers need HTTPS except on `localhost`, so serve it behind a TLS proxy if you want the PWA install on other devices.

## Contributing

Issues and pull requests are welcome. Please keep to the product constraints in `CLAUDE.md` (kid-sized explanations, no sound, parent-configurable rates, all text via `t()`) and add a test for any change to money logic.

## License

[MIT](LICENSE)
