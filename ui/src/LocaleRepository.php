<?php

declare(strict_types=1);

final class LocaleRepository
{
    public function __construct(private PDO $pdo)
    {
    }

    public function stats(): array
    {
        return [
            'canonical' => $this->countUnits('u.canonical = 1'),
            'extended' => $this->countUnits('u.extended = 1'),
            'ready' => $this->countReady('u.extended = 1'),
            'pending' => $this->countPending('u.extended = 1'),
            'reviewed' => $this->countReviewed('u.extended = 1'),
        ];
    }

    public function listUnits(array $filters): array
    {
        [$where, $params] = $this->where($filters);
        $limit = max(1, min((int) $filters['page_size'], 100));
        $offset = max(0, ((int) $filters['page'] - 1) * $limit);

        $totalSql = "SELECT COUNT(*) FROM locale_units u {$where}";
        $stmt = $this->pdo->prepare($totalSql);
        $stmt->execute($params);
        $total = (int) $stmt->fetchColumn();

        $sql = "
            SELECT
                u.unit_id,
                u.source_hash,
                u.source,
                u.datatype,
                u.canonical,
                u.extended,
                bt.target,
                bt.origin,
                bt.provider,
                bt.model,
                bt.quality_status,
                bt.is_reviewed,
                bt.updated_at AS target_updated_at
            FROM locale_units u
            LEFT JOIN locale_targets bt ON bt.target_id = (
                SELECT t.target_id
                FROM locale_targets t
                WHERE t.unit_id = u.unit_id
                  AND t.source_hash = u.source_hash
                  AND t.quality_status IN ('valid', 'approved')
                ORDER BY
                  CASE
                    WHEN t.origin = 'manual' AND t.is_reviewed = 1 THEN 1
                    WHEN t.origin = 'ai_cache' AND t.quality_status = 'approved' THEN 2
                    WHEN t.origin = 'ai_cache' THEN 3
                    WHEN t.origin = 'cpanel' THEN 4
                    ELSE 5
                  END,
                  t.updated_at DESC,
                  t.target_id DESC
                LIMIT 1
            )
            {$where}
            ORDER BY u.canonical DESC, u.unit_id ASC
            LIMIT :limit OFFSET :offset
        ";

        $stmt = $this->pdo->prepare($sql);
        foreach ($params as $key => $value) {
            $stmt->bindValue($key, $value);
        }
        $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
        $stmt->execute();

        return [
            'total' => $total,
            'items' => $stmt->fetchAll(),
        ];
    }

    public function findUnit(string $unitId, string $sourceHash): ?array
    {
        $stmt = $this->pdo->prepare(
            "
            SELECT
                u.*,
                bt.target,
                bt.target_xml,
                bt.origin,
                bt.provider,
                bt.model,
                bt.quality_status,
                bt.is_reviewed
            FROM locale_units u
            LEFT JOIN locale_targets bt ON bt.target_id = (
                SELECT t.target_id
                FROM locale_targets t
                WHERE t.unit_id = u.unit_id
                  AND t.source_hash = u.source_hash
                  AND t.quality_status IN ('valid', 'approved')
                ORDER BY
                  CASE
                    WHEN t.origin = 'manual' AND t.is_reviewed = 1 THEN 1
                    WHEN t.origin = 'ai_cache' AND t.quality_status = 'approved' THEN 2
                    WHEN t.origin = 'ai_cache' THEN 3
                    WHEN t.origin = 'cpanel' THEN 4
                    ELSE 5
                  END,
                  t.updated_at DESC,
                  t.target_id DESC
                LIMIT 1
            )
            WHERE u.unit_id = :unit_id AND u.source_hash = :source_hash
            "
        );
        $stmt->execute([':unit_id' => $unitId, ':source_hash' => $sourceHash]);
        $row = $stmt->fetch();

        return $row ?: null;
    }

    public function saveManualTarget(string $unitId, string $sourceHash, string $target): void
    {
        $target = trim($target);
        if ($target === '') {
            throw new InvalidArgumentException('A traducao manual nao pode ficar vazia.');
        }

        $targetXml = $this->targetXml($target);
        $stmt = $this->pdo->prepare(
            "
            INSERT INTO locale_targets (
                unit_id, source_hash, target, target_xml, target_attrs_json,
                provider, model, origin, quality_status, is_reviewed, source_file
            )
            VALUES (
                :unit_id, :source_hash, :target, :target_xml, :target_attrs_json,
                'human', '', 'manual', 'valid', 1, 'ui'
            )
            ON CONFLICT(unit_id, source_hash, origin, provider, model, source_file)
            DO UPDATE SET
                target = excluded.target,
                target_xml = excluded.target_xml,
                target_attrs_json = excluded.target_attrs_json,
                quality_status = 'valid',
                is_reviewed = 1,
                updated_at = CURRENT_TIMESTAMP
            "
        );
        $stmt->execute([
            ':unit_id' => $unitId,
            ':source_hash' => $sourceHash,
            ':target' => $target,
            ':target_xml' => $targetXml,
            ':target_attrs_json' => json_encode([
                'state' => 'translated',
                '{tag:cpanel.net,2012-01:translate}origin' => 'manual',
            ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
        ]);
    }

    private function targetXml(string $target): string
    {
        $escaped = htmlspecialchars($target, ENT_XML1 | ENT_COMPAT, 'UTF-8');
        return '<target xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:cp="tag:cpanel.net,2012-01:translate" state="translated" cp:origin="manual">' . $escaped . '</target>';
    }

    private function countUnits(string $where): int
    {
        return (int) $this->pdo->query("SELECT COUNT(DISTINCT u.unit_id) FROM locale_units u WHERE {$where}")->fetchColumn();
    }

    private function countReady(string $where): int
    {
        return (int) $this->pdo->query(
            "
            SELECT COUNT(DISTINCT u.unit_id)
            FROM locale_units u
            JOIN locale_targets t ON t.unit_id = u.unit_id AND t.source_hash = u.source_hash
            WHERE {$where}
              AND t.quality_status IN ('valid', 'approved')
              AND t.origin IN ('manual', 'ai_cache', 'cpanel')
            "
        )->fetchColumn();
    }

    private function countReviewed(string $where): int
    {
        return (int) $this->pdo->query(
            "
            SELECT COUNT(DISTINCT u.unit_id)
            FROM locale_units u
            JOIN locale_targets t ON t.unit_id = u.unit_id AND t.source_hash = u.source_hash
            WHERE {$where}
              AND t.is_reviewed = 1
              AND t.quality_status IN ('valid', 'approved')
            "
        )->fetchColumn();
    }

    private function countPending(string $where): int
    {
        return max($this->countUnits($where) - $this->countReady($where), 0);
    }

    private function where(array $filters): array
    {
        $clauses = ['u.extended = 1'];
        $params = [];

        if (($filters['scope'] ?? 'extended') === 'canonical') {
            $clauses = ['u.canonical = 1'];
        }
        $rankScope = $clauses[0] === 'u.canonical = 1' ? 'u2.canonical = 1' : 'u2.extended = 1';
        $clauses[] = "u.source_hash = (
            SELECT u2.source_hash
            FROM locale_units u2
            WHERE u2.unit_id = u.unit_id
              AND {$rankScope}
            ORDER BY
              u2.canonical DESC,
              CASE
                WHEN EXISTS (
                  SELECT 1
                  FROM locale_targets bt
                  WHERE bt.unit_id = u2.unit_id
                    AND bt.source_hash = u2.source_hash
                    AND bt.quality_status IN ('valid', 'approved')
                    AND bt.origin IN ('manual', 'ai_cache', 'cpanel')
                ) THEN 0
                ELSE 1
              END,
              u2.updated_at DESC,
              u2.source_hash DESC
            LIMIT 1
        )";

        $search = trim((string) ($filters['search'] ?? ''));
        if ($search !== '') {
            $clauses[] = "(
                u.unit_id LIKE :search
                OR u.source_hash LIKE :search
                OR u.source LIKE :search
                OR EXISTS (
                    SELECT 1
                    FROM locale_targets st
                    WHERE st.unit_id = u.unit_id
                      AND st.source_hash = u.source_hash
                      AND st.target LIKE :search
                )
            )";
            $params[':search'] = '%' . $search . '%';
        }

        $status = (string) ($filters['status'] ?? '');
        if ($status === 'pending') {
            $clauses[] = "NOT EXISTS (
                SELECT 1
                FROM locale_targets pt
                WHERE pt.unit_id = u.unit_id
                  AND pt.source_hash = u.source_hash
                  AND pt.quality_status IN ('valid', 'approved')
                  AND pt.origin IN ('manual', 'ai_cache', 'cpanel')
            )";
        } elseif (in_array($status, ['ai_cache', 'cpanel', 'manual'], true)) {
            $clauses[] = "EXISTS (
                SELECT 1
                FROM locale_targets ot
                WHERE ot.unit_id = u.unit_id
                  AND ot.source_hash = u.source_hash
                  AND ot.origin = :origin
                  AND ot.quality_status IN ('valid', 'approved')
            )";
            $params[':origin'] = $status;
        } elseif ($status === 'reviewed') {
            $clauses[] = "EXISTS (
                SELECT 1
                FROM locale_targets rt
                WHERE rt.unit_id = u.unit_id
                  AND rt.source_hash = u.source_hash
                  AND rt.is_reviewed = 1
                  AND rt.quality_status IN ('valid', 'approved')
            )";
        }

        return ['WHERE ' . implode(' AND ', $clauses), $params];
    }
}
