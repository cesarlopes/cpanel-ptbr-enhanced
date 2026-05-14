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
            'canonical' => $this->countStatus('s.current_canonical = 1'),
            'extended' => $this->countStatus('s.current_extended = 1'),
            'ready' => $this->countStatus('s.current_extended = 1 AND s.ready = 1'),
            'pending' => $this->countStatus('s.current_extended = 1 AND s.ready = 0'),
            'reviewed' => $this->countStatus('s.current_extended = 1 AND s.reviewed = 1'),
        ];
    }

    public function listUnits(array $filters): array
    {
        [$where, $params] = $this->where($filters);
        $limit = max(1, min((int) $filters['page_size'], 100));
        $offset = max(0, ((int) $filters['page'] - 1) * $limit);

        $stmt = $this->pdo->prepare("SELECT COUNT(*) FROM locale_unit_status s {$where}");
        $stmt->execute($params);
        $total = (int) $stmt->fetchColumn();

        $sql = "
            SELECT
                s.unit_id,
                s.source_hash,
                s.source,
                s.datatype,
                s.canonical,
                s.extended,
                s.target,
                s.origin,
                s.provider,
                s.model,
                s.quality_status,
                s.is_reviewed,
                s.target_updated_at
            FROM locale_unit_status s
            {$where}
            ORDER BY s.canonical DESC, s.unit_id ASC
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
                s.target,
                s.target_xml,
                s.origin,
                s.provider,
                s.model,
                s.quality_status,
                s.is_reviewed,
                COALESCE(cpanel_exact.target, cpanel_any.target) AS cpanel_target,
                COALESCE(cpanel_exact.source_hash, cpanel_any.source_hash) AS cpanel_source_hash
            FROM locale_units u
            LEFT JOIN locale_unit_status s
              ON s.unit_id = u.unit_id
             AND s.source_hash = u.source_hash
            LEFT JOIN locale_targets cpanel_exact ON cpanel_exact.target_id = (
                SELECT ct.target_id
                FROM locale_targets ct
                WHERE ct.unit_id = u.unit_id
                  AND ct.source_hash = u.source_hash
                  AND ct.origin = 'cpanel'
                  AND ct.quality_status = 'valid'
                ORDER BY ct.updated_at DESC, ct.target_id DESC
                LIMIT 1
            )
            LEFT JOIN locale_targets cpanel_any ON cpanel_any.target_id = (
                SELECT ct.target_id
                FROM locale_targets ct
                WHERE ct.unit_id = u.unit_id
                  AND ct.origin = 'cpanel'
                  AND ct.quality_status = 'valid'
                ORDER BY ct.updated_at DESC, ct.target_id DESC
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

        $this->refreshStatusForUnit($unitId);
    }

    private function targetXml(string $target): string
    {
        $escaped = htmlspecialchars($target, ENT_XML1 | ENT_COMPAT, 'UTF-8');
        return '<target xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:cp="tag:cpanel.net,2012-01:translate" state="translated" cp:origin="manual">' . $escaped . '</target>';
    }

    private function countStatus(string $where): int
    {
        return (int) $this->pdo->query("SELECT COUNT(*) FROM locale_unit_status s WHERE {$where}")->fetchColumn();
    }

    private function where(array $filters): array
    {
        $clauses = ['s.current_extended = 1'];
        $params = [];

        if (($filters['scope'] ?? 'extended') === 'canonical') {
            $clauses = ['s.current_canonical = 1'];
        }

        $search = trim((string) ($filters['search'] ?? ''));
        if ($search !== '') {
            $clauses[] = "(
                s.unit_id LIKE :search
                OR s.source_hash LIKE :search
                OR s.source LIKE :search
                OR s.target LIKE :search
            )";
            $params[':search'] = '%' . $search . '%';
        }

        $status = (string) ($filters['status'] ?? '');
        if ($status === 'pending') {
            $clauses[] = 's.ready = 0';
        } elseif (in_array($status, ['ai_cache', 'cpanel', 'manual'], true)) {
            $clauses[] = 's.origin = :origin';
            $params[':origin'] = $status;
        } elseif ($status === 'reviewed') {
            $clauses[] = 's.reviewed = 1';
        }

        return ['WHERE ' . implode(' AND ', $clauses), $params];
    }

    private function refreshStatusForUnit(string $unitId): void
    {
        $this->pdo->prepare('DELETE FROM locale_unit_status WHERE unit_id = :unit_id')
            ->execute([':unit_id' => $unitId]);

        $stmt = $this->pdo->prepare(
            "
            WITH best_targets AS (
                SELECT *
                FROM (
                    SELECT
                        t.target_id,
                        t.unit_id,
                        t.source_hash,
                        t.target,
                        t.target_xml,
                        t.origin,
                        t.provider,
                        t.model,
                        t.quality_status,
                        t.is_reviewed,
                        t.updated_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY t.unit_id, t.source_hash
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
                        ) AS rn
                    FROM locale_targets t
                    WHERE t.quality_status IN ('valid', 'approved')
                      AND t.origin IN ('manual', 'ai_cache', 'cpanel')
                      AND t.unit_id = :unit_id
                )
                WHERE rn = 1
            ),
            canonical_rank AS (
                SELECT
                    u.unit_id,
                    u.source_hash,
                    ROW_NUMBER() OVER (
                        PARTITION BY u.unit_id
                        ORDER BY
                            CASE WHEN bt.target_id IS NOT NULL THEN 0 ELSE 1 END,
                            u.updated_at DESC,
                            u.source_hash DESC
                    ) AS rn
                FROM locale_units u
                LEFT JOIN best_targets bt
                  ON bt.unit_id = u.unit_id
                 AND bt.source_hash = u.source_hash
                WHERE u.canonical = 1
                  AND u.unit_id = :unit_id
            ),
            extended_rank AS (
                SELECT
                    u.unit_id,
                    u.source_hash,
                    ROW_NUMBER() OVER (
                        PARTITION BY u.unit_id
                        ORDER BY
                            u.canonical DESC,
                            CASE WHEN bt.target_id IS NOT NULL THEN 0 ELSE 1 END,
                            u.updated_at DESC,
                            u.source_hash DESC
                    ) AS rn
                FROM locale_units u
                LEFT JOIN best_targets bt
                  ON bt.unit_id = u.unit_id
                 AND bt.source_hash = u.source_hash
                WHERE u.extended = 1
                  AND u.unit_id = :unit_id
            )
            INSERT INTO locale_unit_status (
                unit_id, source_hash, source, source_xml, datatype,
                canonical, extended, current_canonical, current_extended,
                target_id, target, target_xml, origin, provider, model,
                quality_status, is_reviewed, ready, reviewed, status,
                target_updated_at, unit_updated_at, refreshed_at
            )
            SELECT
                u.unit_id,
                u.source_hash,
                u.source,
                u.source_xml,
                u.datatype,
                u.canonical,
                u.extended,
                CASE WHEN cr.rn = 1 THEN 1 ELSE 0 END,
                CASE WHEN er.rn = 1 THEN 1 ELSE 0 END,
                bt.target_id,
                bt.target,
                bt.target_xml,
                bt.origin,
                bt.provider,
                bt.model,
                bt.quality_status,
                COALESCE(bt.is_reviewed, 0),
                CASE WHEN bt.target_id IS NOT NULL THEN 1 ELSE 0 END,
                CASE WHEN COALESCE(bt.is_reviewed, 0) = 1 THEN 1 ELSE 0 END,
                COALESCE(bt.origin, 'pending'),
                bt.updated_at,
                u.updated_at,
                CURRENT_TIMESTAMP
            FROM locale_units u
            LEFT JOIN best_targets bt
              ON bt.unit_id = u.unit_id
             AND bt.source_hash = u.source_hash
            LEFT JOIN canonical_rank cr
              ON cr.unit_id = u.unit_id
             AND cr.source_hash = u.source_hash
            LEFT JOIN extended_rank er
              ON er.unit_id = u.unit_id
             AND er.source_hash = u.source_hash
            WHERE u.unit_id = :unit_id
            "
        );
        $stmt->execute([':unit_id' => $unitId]);
    }
}
