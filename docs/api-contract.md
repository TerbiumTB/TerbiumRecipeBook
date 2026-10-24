# HTTP-контракт v1

Статус: проект для реализации. Этот документ — общий контракт фронтенда, бэкенда и модуля модели. Prefix: `/api/v1`. JSON использует camelCase, даты — UTC ISO 8601, серверные ID — UUID. Каноническое содержимое рецепта определено в `schemas/recipe.schema.json`.

## Общие правила

Все маршруты, кроме входа, требуют сессию. Вход `POST /auth/telegram {initData}` проверяет Telegram-подпись и разрешённый Telegram ID, возвращает `{user:{telegramId}}` и HttpOnly/Secure/SameSite cookie. `GET /me` возвращает тот же user, `POST /auth/logout` завершает сессию (204). Cookie остаётся внутри браузера; токен не передаётся компонентам фронтенда.

Списки возвращают `{items,nextCursor}`, где nextCursor nullable. `limit` по умолчанию 30, допустимо 1–100. Cursor непрозрачный и относится к конкретным фильтрам/сортировке. Поля ошибок: `{error:{code,message,requestId,details}}`, details nullable или массив `{path,message}`. Ошибки не содержат токенов и сырых ответов провайдера.

RecipeRecord:

```ts
type RecipeRecord = {
  id: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
  source: "manual" | "llm";
  sourceConversationId: string | null;
  content: RecipeContent;
};
```

ETag одиночного рецепта имеет вид `"recipe:<uuid>:<revision>"`. Изменение и удаление требуют If-Match именно этого ETag; отсутствие — 428 `revision_required`, устаревшая версия — 412 `revision_conflict`. Последовательные изменения заметок тоже увеличивают revision. Ответ успешной записи содержит актуальный ETag. Revision из сообщения клиента не разрешает обход проверок версии.

## Рецепты

- `GET /recipes?q=&totalMinutesMax=&activeMinutesMax=&productIds=...&limit=&cursor=`. Несколько повторяемых productIds означают наличие всех выбранных продуктов. Items: `{id,revision,title,servings,time,updatedAt}`. Сортировка updatedAt DESC, id DESC. Неизвестный productId даёт пустую выборку, неверный формат — 422.
- `POST /recipes`: тело RecipeContent; только ручное создание, source выставляется сервером. Ответ 201 RecipeRecord, Location и ETag.
- `GET /recipes/{id}`: RecipeRecord + ETag.
- `PUT /recipes/{id}`: тело RecipeContent, If-Match; ручное полное редактирование, 200 RecipeRecord.
- `PATCH /recipes/{id}/notes`: `{userNotes}`, If-Match; 200 RecipeRecord.
- `DELETE /recipes/{id}`: If-Match; 204. Чат сохраняется.
- `GET /products?q=&limit=&cursor=`: items `{id,name}`; поиск по имени/alias.

Ручная незавершённая форма остаётся локально. Поиск и сохранение работают без подключения модели. Кнопка изменения порций меняет только локальное представление количества.

## Чаты и генерация

- `POST /conversations {recipeId:null|string}` → 201 `{id,recipeId,createdAt,updatedAt}`.
- `GET /conversations?limit=&cursor=` → список тех же метаданных.
- `GET /conversations/{id}` → метаданные.
- `DELETE /conversations/{id}` → 204. Удаляет сообщения, запросы генерации и черновики; сохранённые рецепты остаются, sourceConversationId обнуляется. При running возвращает 409 conversation_busy; проверка и удаление атомарны относительно начала нового запроса.
- `GET /conversations/{id}/messages?limit=&cursor=` → сообщения в порядке sequence ASC; items `{id,sequence,role,createdAt,text,reply}`. Для user text заполнен, reply=null; для assistant text=null и reply имеет формат AssistantReply ниже. Неудачный запрос не создаёт фиктивный assistant message.
- `POST /conversations/{id}/messages` принимает тело ниже и возвращает 200 AssistantReply после завершения. Streaming в первом контракте отсутствует.

```ts
type SendMessage = {
  clientRequestId: string; // UUID, постоянный при повторе одного запроса
  text: string;
  selectedIdea: {messageId: string; ideaId: string} | null;
  recipeContext:
    | null
    | { recipeId: string; revision: number }
    | { draftId: string };
};

type GeneratedRecipeContent = Omit<RecipeContent, "userNotes">;

type AssistantReply = {
  requestId: string;
  userMessageId: string;
  assistantMessageId: string;
  createdAt: string;
  message: string;
  ideas: Array<{id: string; title: string; description: string}>;
  recipeDraft: {
    id: string;
    baseRecipeId: string | null;
    baseRevision: number | null;
    content: GeneratedRecipeContent;
  } | null;
};
```

Все серверные ID, даты, связи и schemaVersion назначает бэкенд. Внутренний RecipeDraft модели не содержит userNotes и schemaVersion; API content добавляет schemaVersion после проверки. `recipeContext` всегда явный: сохранённый рецепт, предыдущий черновик или null. При контексте draft наследуем исходные baseRecipeId/baseRevision; заметки из контекста исключаем. Draft должен относиться к этому чату. При recipeId сервер проверяет revision до вызова модели; несовпадение возвращает 412. Новое изменение сохранённого рецепта во время генерации не портит ответ: draft остаётся привязан к старой baseRevision и не может незаметно заменить новый рецепт.

clientRequestId уникален для пользователя во всех чатах. Повтор в другом чате — 409 idempotency_conflict. selectedIdea проверяется на существование идеи в указанном assistant message этого чата; выбор передаётся модели по серверному содержимому идеи. Повтор с тем же телом возвращает готовый ответ либо 202 `{requestId,status:"running"}`; с другим телом — 409 `idempotency_conflict`. Серверный requestId для генерации равен clientRequestId, поэтому известен клиенту даже при обрыве соединения. Новый запрос в занятом чате — 409 `conversation_busy`. Пользовательское сообщение записывается один раз. Повтор clientRequestId для failed не запускает новый платный вызов; возвращается сохранённая ошибка. Явная повторная попытка получает новый clientRequestId.

`GET /generation-requests/{requestId}` возвращает `{requestId,status,reply,error}`. Status: running, succeeded, failed, interrupted; reply заполнен только для succeeded, error — для failed/interrupted. Результат принадлежит единственному авторизованному пользователю. Нет durable worker: при перезапуске running переводится в interrupted, автоматического перезапуска вызова нет. Этот маршрут восстанавливает результат после сетевого сбоя, но не обещает завершения генерации после рестарта сервера.

## Сохранение предложения модели

`POST /drafts/{id}/save {targetRecipeId:null|string}` требует Idempotency-Key (UUID). Для создания ответ 201 RecipeRecord. Для замены ответ 200 RecipeRecord и обязателен If-Match целевого рецепта.

При target=null сервер берёт проверенный draft и добавляет userNotes="". При замене target обязан совпадать с baseRecipeId; baseRevision и If-Match должны соответствовать текущей revision. Несовпадение версии — 412, другой target — 422. Заметки берутся из существующей записи и сохраняются. Применить draft нового рецепта поверх произвольного сохранённого рецепта этим методом нельзя.

Если рецепт изменился после создания draft, UI предлагает сохранить результат новым рецептом или повторить генерацию на актуальной версии; автоматически подставлять свежий ETag запрещено. Fingerprint идемпотентности включает draft ID, targetRecipeId и If-Match. Повтор того же ключа и fingerprint возвращает исходный ответ без нового рецепта/ревизии; другое тело с тем же ключом — 409. Проверка уже сохранённого ключа выполняется до проверки текущей revision, чтобы повтор успешной операции работал. Один draft сохраняется один раз: повтор с другим ключом возвращает прежнюю запись, если цель совпадает, иначе 409 `draft_already_saved`. После удаления этой записи возвращается 409 `saved_recipe_deleted`; для нового сохранения нужен новый draft.

## Коды ошибок

| HTTP | code | Смысл |
|---|---|---|
| 401 | unauthorized | Нет или истекла сессия/невалидные данные входа |
| 403 | forbidden | Пользователь или Origin не разрешён |
| 404 | not_found | Ресурс отсутствует |
| 409 | conversation_busy, idempotency_conflict, draft_already_saved, saved_recipe_deleted | Конфликт операции |
| 412 | revision_conflict | Устарела версия рецепта |
| 422 | validation_error | Ошибка входных данных |
| 428 | revision_required | Нет обязательного If-Match |
| 429 | rate_limited | Достигнут лимит запросов |
| 502 | model_error, model_invalid_response, model_refusal | Модель не дала пригодного результата |
| 504 | model_timeout | Истекло время ожидания |

Объяснение отказа показывается пользователю безопасным текстом; рецепт не меняется. Успешный обычный совет без рецепта имеет recipeDraft=null и не считается ошибкой.
