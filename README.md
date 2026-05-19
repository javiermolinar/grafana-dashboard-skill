# Grafana Dashboard Skill

Grafana CLI ([GCX](https://github.com/grafana/gcx)) is a great tool for creating dashboards. It lets your fav agent iterate on your idea. The problem is that your agent sees the JSON, not what it really looks like.

This is an agent skill, not a Grafana plugin.

This skill is your agent's eyes. It will allow it to:

1. create or update dashboards,
2. verify the saved dashboard,
3. review the visual result and iterate.


## Typical agent prompt

```text
Create a query-path triage dashboard in the Platform folder.
Use existing dashboards in this Grafana instance as examples.
Validate the saved dashboard and take a screenshot before finishing.
```
<img width="1416" height="594" alt="Screenshot 2026-05-19 at 23 45 39" src="https://github.com/user-attachments/assets/25b1a37a-560c-41a9-a29d-1142664adbbe" />


## Requirements

- [`gcx`](https://github.com/grafana/gcx) CLI configured for the target Grafana instance
- Python 3.10+
- Python Playwright
- Chromium installed through Playwright

## Install the skill

Ask your agent to install the `make-grafana-dashboards` skill locally.

Manual install:

```bash
mkdir -p ~/.agents/skills
ln -s "$PWD/make-grafana-dashboards" ~/.agents/skills/make-grafana-dashboards
```

### Screenshot auth

Used for screenshots.

Browser login is stored in a persistent Playwright profile:

```text
~/.cache/grafana-dashboard-screenshot/<profile-name>
```

Treat this directory like credentials. Do not commit it.

## License

MIT. See [LICENSE](LICENSE).
