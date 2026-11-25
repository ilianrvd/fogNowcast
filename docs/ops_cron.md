# Планирано пускане през cron-job.org

GitHub Actions не пуска редовно собствения си `schedule` (изпълненията се бавят и се пропускат), затова workflow-ите се задействат от [cron-job.org](https://cron-job.org) през `workflow_dispatch`.

## `ingest-ntu-live` (на 15 мин)

Правило на НТУ: най-много една заявка към `/mto/data` на 15 мин. Всяко изпълнение прави точно една.

**Токен.** GitHub → Settings → Developer settings → Fine-grained personal access tokens. Собственик: `ilianrvd`. Достъп само до repo `fogNowcast`. Права: **Actions: Read and write**, друго нищо.

**Задача в cron-job.org.**

| Поле | Стойност |
|---|---|
| URL | `https://api.github.com/repos/ilianrvd/fogNowcast/actions/workflows/ingest-ntu-live.yml/dispatches` |
| Метод | `POST` |
| Тяло | `{"ref":"main"}` |
| Заглавия | `Authorization: Bearer <токен>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`, `User-Agent: cron-job.org` |
| Разписание | на 15 мин, със отместване 5 мин: минути 5, 20, 35, 50 |
| Успешен отговор | HTTP 204 |

Отместването е, защото НТУ публикува на :00, :15, :30, :45 и записът се появява след няколко минути.

**Pages.** Settings → Pages → Source: *Deploy from a branch*, клон `gh-pages`, папка `/ (root)`. След първото изпълнение файловете са на `https://ilianrvd.github.io/fogNowcast/obs/ntu_latest.json`. Pages връща `Cache-Control: max-age=600`, така че клиентът може да види до 10 мин по-стари данни.

Токенът не се записва в repo-то. Ако изтече, задачата в cron-job.org връща 401.
