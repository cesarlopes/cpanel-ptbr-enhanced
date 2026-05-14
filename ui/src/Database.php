<?php

declare(strict_types=1);

final class Database
{
    public static function connect(string $path): PDO
    {
        if (!is_file($path)) {
            throw new RuntimeException("SQLite database not found: {$path}");
        }

        $pdo = new PDO('sqlite:' . $path);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        $pdo->exec('PRAGMA foreign_keys = ON');

        return $pdo;
    }
}

