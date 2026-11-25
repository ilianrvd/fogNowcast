# Планирано пускане през cron-job.org

GitHub Actions не пуска редовно собствения си `schedule` (изпълненията се бавят и се пропускат), затова workflow-ите се задействат от [cron-job.org](https://cron-job.org) през `workflow_dispatch`.

## `ingest-ntu-live` (на 15 мин)

Правило на НТУ: най-много една заявка към `/mto/data` на 15 мин. Всяко изпълнение прави точно една заявка към `/mto/data` (и една към `/temperaturefallback`, който се обновява също на 15 мин).

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

## Наблюдение: да разберем, ако данните спрат

Две независими нива (от одита на етап 1, 2026-11-25).

**1. Известие в cron-job.org.** В задачата `ingest-ntu-live` (раздел *Notifications*) се включва известие при неуспешно изпълнение и при спиране на задачата заради много неуспехи. Това хваща изтекъл токен (HTTP 401), срив на GitHub API и спряна задача. Не хваща изпълнение, което GitHub е приел (204), но което после пада.

**2. Workflow `health` (на всеки час).** Втора задача в cron-job.org, със същите заглавия и токен като горната:

| Поле | Стойност |
|---|---|
| URL | `https://api.github.com/repos/ilianrvd/fogNowcast/actions/workflows/health.yml/dispatches` |
| Метод, тяло | `POST`, `{"ref":"main"}` |
| Разписание | веднъж на час, минута 40 (между изпълненията на ingest) |

Workflow-ът не прави заявки към НТУ. Чете от Pages `obs/ntu_latest.json` и `obs/ntu_fetch_state.json` и датите на клоновете `archive-live` и `archive-fallback`. Сигнал за проблем е:

- `ntu_latest.json` или последната заявка към НТУ са по-стари от 45 мин (15 мин цикъл + 10 мин кеш на Pages + едно пропуснато изпълнение);
- клон `archive-live` или `archive-fallback` не е обновяван от 60 мин;
- под 50 станции (нормално ~95);
- над 50 % от станциите с `late` или `fallback_unknown`, над 20 % с `t_frozen`;
- някой от файловете не може да се прочете.

При проблем се отваря issue „fogNowcast: данните са спрели“ (или се добавя коментар към вече отвореното); когато всичко е наред, issue се затваря с последния доклад. GitHub праща имейл на собственика на репото за новите issues и коментари.

**Какво не се хваща:** ако спре самият cron-job.org, не тръгва нито ingest, нито health. Това се вижда само по известията на cron-job.org (ниво 1) и по възрастта на данните при потребителите.
