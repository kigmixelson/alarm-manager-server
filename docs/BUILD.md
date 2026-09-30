
Сборка на машине с интернетом и Docker (amd64 — Thick с Oracle Instant Client):
```bash
bash scripts/build-offline-bundle.sh linux/amd64 2026.09.14-1
```
Комплект появится в dist/. На целевом сервере нужны Docker и Compose.
Образ без Instant Client: добавьте аргумент `thin`.

