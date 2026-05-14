<?php

declare(strict_types=1);

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function project_root(): string
{
    return dirname(__DIR__, 2);
}

function db_path(): string
{
    $envPath = getenv('LOCALE_DB_PATH');
    if ($envPath !== false && $envPath !== '') {
        return $envPath;
    }

    return project_root() . DIRECTORY_SEPARATOR . 'cache' . DIRECTORY_SEPARATOR . 'translations.sqlite';
}

function current_url(array $overrides = []): string
{
    $params = $_GET;
    foreach ($overrides as $key => $value) {
        if ($value === null) {
            unset($params[$key]);
        } else {
            $params[$key] = (string) $value;
        }
    }

    $query = http_build_query($params);
    return 'index.php' . ($query !== '' ? '?' . $query : '');
}

function truncate_text(string $text, int $length = 160): string
{
    if (mb_strlen($text, 'UTF-8') <= $length) {
        return $text;
    }

    return mb_substr($text, 0, $length - 1, 'UTF-8') . '...';
}

