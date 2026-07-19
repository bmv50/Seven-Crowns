# Генерация изображений (ComfyUI API)

## Подключение
- API: http://127.0.0.1:8188 (если из WSL2 — IP хоста Windows, ComfyUI запущен с --listen 0.0.0.0)
- Шаблон воркфлоу: workflow_api.json
- Подменять: текст в ноде CLIPTextEncode (positive), seed в KSampler
- Отправка: POST /prompt, тело {"prompt": <workflow_json>}
- Результат: GET /history/{prompt_id}, файл через GET /view

## Параметры (НЕ менять)
- Модель: DreamShaper XL Turbo
- Steps: 7, CFG: 2, sampler: dpmpp_sde, scheduler: karras
- Иконки предметов/мобов: 768x768
- Локации/сцены: 1216x704

## Стилевой шаблон промпта
Positive: "fantasy game art, dark medieval style, painterly digital illustration,
{описание сущности}, centered composition, detailed, atmospheric lighting"
Negative: "photo, photorealistic, text, watermark, blurry, low quality,
modern objects, cartoon, anime"

## Правила
- Перед началом генерации проверь доступность API запросом GET /system_stats. Если не отвечает — останови работу и попроси пользователя запустить ComfyUI
- Имя файла = ID сущности из YAML (iron_sword.png)
- Seed фиксировать и записывать в YAML сущности (для регенерации)
- Генерировать последовательно, не параллельно (одна GPU)