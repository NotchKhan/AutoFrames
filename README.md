# AutoFrames

AutoFrames автоматически собирает длинное видео из изображений и одной аудиодорожки. Время окончания каждого кадра берётся из временной метки в начале имени файла, кадры сортируются по числовому времени, а итоговый MP4 создаётся через FFmpeg в формате H.264/AAC.

Проект разделён на три независимые части:

- `frontend/` — Next.js 16, TypeScript и App Router; размещается на Vercel;
- `backend/` — FastAPI, Pillow, ffprobe и FFmpeg; размещается отдельным Docker-сервисом на Railway или Render;
- `legacy_streamlit/` — прежняя полностью локальная Streamlit-версия как резервный вариант.

FFmpeg-рендер никогда не запускается внутри Vercel Functions. Браузер отправляет файлы напрямую в FastAPI, получает состояние задачи по API и скачивает готовый MP4 с backend-сервера.

## Как устроено время кадров

Поддерживаемые метки:

```text
[MM-SS]
[MM-SS.mmm]
[HH-MM-SS]
[HH-MM-SS.mmm]
```

Примеры имён:

```text
[0-05]_первый кадр.jpg
[0-14]_scene two.webp
[1-02.250]_третий-кадр.png
[01-02-15.750]_длинный ролик.jpeg
```

Метка означает точное время **окончания** текущего кадра. Начало вычисляется автоматически:

```text
[0-05]  00:00:00.000 → 00:00:05.000
[0-14]  00:00:05.000 → 00:00:14.000
[1-02]  00:00:14.000 → 00:01:02.000
```

Секунды всегда находятся в диапазоне `00–59`. В часовом формате минуты тоже должны быть `00–59`. Файлы с одинаковым временем окончания отклоняются как некорректный таймлайн.

## Структура репозитория

```text
AutoFrames/
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   ├── public/
│   ├── Dockerfile
│   ├── package.json
│   └── vercel.json
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── services/
│   │   ├── utils/
│   │   └── main.py
│   ├── tests/
│   ├── Dockerfile
│   ├── railway.json
│   ├── render.yaml
│   └── requirements.txt
├── legacy_streamlit/
├── docker-compose.yml
├── .gitignore
└── README.md
```

## Возможности web-версии

- множественная загрузка PNG, JPG, JPEG, WEBP и BMP;
- загрузка MP3, WAV, M4A, AAC, OGG или FLAC;
- проверка MIME-типа, расширения, содержимого и временной метки;
- числовая сортировка и непрерывный таймлайн;
- удаление отдельного кадра до рендеринга;
- форматы 16:9, 9:16, 1:1 и пользовательский чётный размер;
- 24, 25, 30 или 60 FPS;
- обрезка, размытый фон или цветной фон;
- Ken Burns, fade и безопасный fade-вариант перехода без смещения таймлайна;
- настройки громкости, нормализации и аудиозатухания;
- фоновый рендеринг, polling прогресса и безопасная отмена FFmpeg;
- финальная проверка кодеков, FPS, размера и длительности;
- скачивание готового MP4.

## API backend

После запуска интерактивная документация доступна по адресам:

- Swagger UI: `http://localhost:8000/docs`;
- ReDoc: `http://localhost:8000/redoc`;
- health-check: `GET http://localhost:8000/health`.

Основные маршруты:

| Метод | Маршрут | Назначение |
|---|---|---|
| `POST` | `/api/projects` | создать проект |
| `POST` | `/api/projects/{id}/images` | загрузить изображения |
| `POST` | `/api/projects/{id}/audio` | загрузить аудио |
| `GET` | `/api/projects/{id}/timeline` | построить и проверить таймлайн |
| `POST` | `/api/projects/{id}/render` | запустить рендеринг |
| `GET` | `/api/projects/{id}/status` | состояние, прогресс и журнал |
| `GET` | `/api/projects/{id}/progress` | компактный прогресс |
| `POST` | `/api/projects/{id}/cancel` | отменить задачу |
| `GET` | `/api/projects/{id}/result` | скачать MP4 |
| `DELETE` | `/api/projects/{id}` | удалить проект и его файлы |

Ошибки API имеют единый безопасный формат:

```json
{
  "error": {
    "code": "invalid_image_timestamp",
    "message": "Понятное описание ошибки",
    "details": {}
  }
}
```

Абсолютные серверные пути клиенту не возвращаются.

## Быстрый локальный запуск через Docker Compose

Требуется Docker Desktop с Compose v2.

```powershell
git clone https://github.com/NotchKhan/AutoFrames.git
cd AutoFrames
docker compose up --build
```

Откройте:

- frontend: `http://localhost:3000`;
- backend API: `http://localhost:8000/docs`.

Остановка:

```powershell
docker compose down
```

Удаление контейнеров вместе с локальным томом проектов:

```powershell
docker compose down --volumes
```

## Локальная разработка без Docker

### Backend

Требуются Python 3.11 или новее, FFmpeg и ffprobe в `PATH`.

Проверка:

```powershell
python --version
ffmpeg -version
ffprobe -version
```

Создание окружения и запуск в PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python entrypoint.py
```

Для CMD активация выполняется командой:

```bat
.venv\Scripts\activate.bat
```

### Frontend

Требуется Node.js 20.9 или новее.

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

В `.env.local` для локального запуска должно быть:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Тесты и проверки

Backend:

```powershell
cd backend
python -m pytest
```

Frontend:

```powershell
cd frontend
npm install
npm run typecheck
npm run lint
npm run build
```

Контейнеры:

```powershell
docker build -t autoframes-backend ./backend
docker compose config
docker compose build
```

Workflow `.github/workflows/ci.yml` повторяет эти проверки при каждом push в `main`: запускает backend-тесты с реальным FFmpeg, проверяет и собирает frontend, валидирует Compose и собирает оба Docker-образа.

## Переменные окружения

### Frontend

| Переменная | Обязательно | Пример |
|---|---:|---|
| `NEXT_PUBLIC_API_URL` | да | `https://api.example.com` |

Значение является публичным и встраивается в клиентский JavaScript во время сборки. В нём нельзя хранить секреты.

### Backend

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `FRONTEND_ORIGIN` | `http://localhost:3000` | разрешённый CORS origin; несколько адресов разделяются запятыми |
| `MAX_FILE_SIZE_MB` | `100` | максимум для одного изображения или аудиофайла |
| `MAX_TOTAL_SIZE_MB` | `2048` | общий максимум проекта |
| `MAX_IMAGE_COUNT` | `500` | максимум изображений проекта |
| `PROJECT_TTL_HOURS` | `24` | срок хранения неактивного проекта |
| `STORAGE_ROOT` | `backend/data` | постоянный каталог проектов, логов и результатов |
| `FFMPEG_BINARY` | поиск в `PATH` | явный путь к FFmpeg |
| `FFPROBE_BINARY` | поиск в `PATH` | явный путь к ffprobe |
| `PORT` | `8000` | порт backend-контейнера |

Готовые примеры находятся в `frontend/.env.example` и `backend/.env.example`. Файлы `.env` в Git не добавляются.

## Публикация проекта в GitHub

Для нового локального репозитория используются точные команды:

```powershell
git init
git add .
git commit -m "Prepare web deployment"
git branch -M main
git remote add origin https://github.com/NotchKhan/AutoFrames.git
git push -u origin main
```

Если `origin` уже существует:

```powershell
git remote set-url origin https://github.com/NotchKhan/AutoFrames.git
git push -u origin main
```

## Размещение frontend на Vercel

1. Откройте Vercel и нажмите **Add New → Project**.
2. Подключите GitHub и выберите репозиторий `NotchKhan/AutoFrames`.
3. В поле **Root Directory** укажите `frontend`.
4. Выберите **Framework Preset: Next.js**.
5. Добавьте переменную `NEXT_PUBLIC_API_URL` со значением публичного HTTPS-адреса backend без завершающего `/`.
6. Нажмите **Deploy**.
7. Скопируйте production-домен Vercel и укажите его в `FRONTEND_ORIGIN` backend-сервиса.
8. После изменения API URL выполните **Redeploy** frontend, потому что `NEXT_PUBLIC_*` встраивается во время сборки.

После подключения репозитория Vercel автоматически создаёт новый deployment при каждом push в отслеживаемую ветку.

## Размещение backend на Railway

1. Создайте в Railway новый проект через **Deploy from GitHub repo**.
2. Выберите тот же репозиторий `NotchKhan/AutoFrames`.
3. В настройках сервиса укажите **Root Directory**: `backend`.
4. Railway обнаружит `Dockerfile` и `railway.json`.
5. Добавьте `FRONTEND_ORIGIN`, `MAX_FILE_SIZE_MB`, `MAX_TOTAL_SIZE_MB`, `MAX_IMAGE_COUNT` и `PROJECT_TTL_HOURS`.
6. Подключите постоянный volume и смонтируйте его в `/data`; установите `STORAGE_ROOT=/data`.
7. Сгенерируйте публичный HTTPS-домен backend.
8. Проверьте `https://BACKEND-DOMAIN/health` и `/docs`.
9. Вставьте домен в `NEXT_PUBLIC_API_URL` проекта Vercel и выполните Redeploy.
10. Оставьте Railway auto-deploy включённым — каждый push в подключённую ветку пересоберёт backend.

## Размещение backend на Render

1. Создайте **New → Web Service** или Blueprint из GitHub.
2. Выберите репозиторий `NotchKhan/AutoFrames`.
3. Для Web Service укажите **Root Directory**: `backend`, среду **Docker** и Dockerfile `./Dockerfile`.
4. Укажите health-check path `/health`.
5. Добавьте backend-переменные окружения.
6. Для сохранения проектов между перезапусками подключите persistent disk в `/data` и задайте `STORAGE_ROOT=/data`.
7. После успешного deploy получите HTTPS URL backend.
8. Добавьте URL в `NEXT_PUBLIC_API_URL` Vercel и выполните Redeploy frontend.
9. Включите **Auto-Deploy: Yes**. Файл `backend/render.yaml` также содержит готовую конфигурацию сервиса.

## Безопасность

- исходное имя используется только для отображения и разбора метки, но никогда не используется как путь записи;
- внутренние файлы получают случайные UUID-имена;
- ID проекта строго проверяется как 32-значный hex;
- Pillow проверяет фактический формат и декодируемость каждого изображения;
- ffprobe проверяет аудиопоток и длительность;
- есть ограничения количества, размера одного файла и общего объёма;
- все subprocess-команды FFmpeg передаются списком аргументов с `shell=False`;
- каждый проект получает отдельные каталоги;
- истёкшие проекты удаляются автоматически;
- CORS разрешён только для `FRONTEND_ORIGIN`;
- секреты и `.env` исключены из Git.

## Резервная Streamlit-версия

Локальная версия сохранена в `legacy_streamlit/`. На Windows:

```powershell
cd legacy_streamlit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

Подробная инструкция и устранение ошибок находятся в `legacy_streamlit/README.md` и `legacy_streamlit/TROUBLESHOOTING.md`.

## Известные ограничения

- текущая очередь задач хранится в памяти одного backend-процесса; используйте одну реплику/worker;
- перезапуск backend отменяет активные рендеры, хотя файлы на persistent volume сохраняются до TTL-очистки;
- для горизонтального масштабирования нужны внешняя очередь задач и общее объектное хранилище;
- большие проекты требуют достаточно CPU, RAM, диска и времени работы контейнера; бесплатные тарифы хостингов могут быть недостаточны;
- browser upload не возобновляется после сетевого разрыва;
- Vercel обслуживает только frontend и не хранит загруженные медиа;
- переход `crossfade_safe` реализован как fade внутри границ каждого смыслового кадра, поэтому точная синхронизация не меняется.
