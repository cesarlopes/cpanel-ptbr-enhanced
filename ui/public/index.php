<?php

declare(strict_types=1);

require_once dirname(__DIR__) . '/src/helpers.php';
require_once dirname(__DIR__) . '/src/Database.php';
require_once dirname(__DIR__) . '/src/LocaleRepository.php';

$error = null;
$notice = null;

try {
    $repo = new LocaleRepository(Database::connect(db_path()));
} catch (Throwable $exception) {
    $repo = null;
    $error = $exception->getMessage();
}

$page = max(1, (int) ($_GET['page'] ?? 1));
$pageSize = max(1, min((int) ($_GET['page_size'] ?? 50), 100));
$filters = [
    'page' => $page,
    'page_size' => $pageSize,
    'search' => (string) ($_GET['search'] ?? ''),
    'status' => (string) ($_GET['status'] ?? ''),
    'scope' => (string) ($_GET['scope'] ?? 'extended'),
];

$requestMethod = $_SERVER['REQUEST_METHOD'] ?? 'GET';
if ($repo !== null && $requestMethod === 'POST') {
    try {
        $repo->saveManualTarget(
            (string) ($_POST['unit_id'] ?? ''),
            (string) ($_POST['source_hash'] ?? ''),
            (string) ($_POST['target'] ?? '')
        );
        $notice = 'Traducao manual salva e marcada como revisada.';
        $_GET['edit'] = (string) ($_POST['unit_id'] ?? '');
        $_GET['hash'] = (string) ($_POST['source_hash'] ?? '');
    } catch (Throwable $exception) {
        $error = $exception->getMessage();
    }
}

$stats = ['canonical' => 0, 'extended' => 0, 'ready' => 0, 'pending' => 0, 'reviewed' => 0];
$result = ['total' => 0, 'items' => []];
$editing = null;

if ($repo !== null) {
    $stats = $repo->stats();
    $result = $repo->listUnits($filters);
    if (isset($_GET['edit'], $_GET['hash'])) {
        $editing = $repo->findUnit((string) $_GET['edit'], (string) $_GET['hash']);
    }
}

$totalPages = max(1, (int) ceil(((int) $result['total']) / $pageSize));
?>
<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>cPanel pt_BR Enhanced</title>
    <link rel="stylesheet" href="assets/styles.css">
</head>
<body>
<main class="shell">
    <header class="topbar">
        <div class="title">
            <h1>cPanel pt_BR Enhanced</h1>
            <p>Revisao local das traducoes XLF no SQLite</p>
        </div>
        <a class="button secondary" href="index.php">Limpar filtros</a>
    </header>

    <?php if ($error !== null): ?>
        <div class="error"><?= h($error) ?></div>
    <?php endif; ?>
    <?php if ($notice !== null): ?>
        <div class="notice"><?= h($notice) ?></div>
    <?php endif; ?>

    <section class="stats">
        <div class="stat"><span>Canonical</span><strong><?= h((string) $stats['canonical']) ?></strong></div>
        <div class="stat"><span>Extended</span><strong><?= h((string) $stats['extended']) ?></strong></div>
        <div class="stat"><span>Prontas</span><strong><?= h((string) $stats['ready']) ?></strong></div>
        <div class="stat"><span>Pendentes</span><strong><?= h((string) $stats['pending']) ?></strong></div>
        <div class="stat"><span>Revisadas</span><strong><?= h((string) $stats['reviewed']) ?></strong></div>
    </section>

    <form class="toolbar" method="get">
        <div>
            <label for="search">Busca</label>
            <input id="search" name="search" value="<?= h($filters['search']) ?>" placeholder="ID, source, target ou hash">
        </div>
        <div>
            <label for="status">Status</label>
            <select id="status" name="status">
                <?php
                $statuses = [
                    '' => 'Todos',
                    'pending' => 'Pendentes',
                    'ai_cache' => 'IA',
                    'cpanel' => 'cPanel',
                    'manual' => 'Manual',
                    'reviewed' => 'Revisadas',
                ];
                foreach ($statuses as $value => $label):
                ?>
                    <option value="<?= h($value) ?>" <?= $filters['status'] === $value ? 'selected' : '' ?>><?= h($label) ?></option>
                <?php endforeach; ?>
            </select>
        </div>
        <div>
            <label for="scope">Escopo</label>
            <select id="scope" name="scope">
                <option value="extended" <?= $filters['scope'] === 'extended' ? 'selected' : '' ?>>Extended</option>
                <option value="canonical" <?= $filters['scope'] === 'canonical' ? 'selected' : '' ?>>Canonical</option>
            </select>
        </div>
        <div>
            <label for="page_size">Por pagina</label>
            <select id="page_size" name="page_size">
                <?php foreach ([25, 50, 100] as $size): ?>
                    <option value="<?= $size ?>" <?= $pageSize === $size ? 'selected' : '' ?>><?= $size ?></option>
                <?php endforeach; ?>
            </select>
        </div>
        <input type="hidden" name="page" value="1">
        <button type="submit">Filtrar</button>
    </form>

    <?php if ($editing !== null): ?>
        <section class="editor">
            <h2>Editar <?= h($editing['unit_id']) ?></h2>
            <p class="muted">Hash: <?= h($editing['source_hash']) ?> · Origem atual: <?= h($editing['origin'] ?? 'pending') ?></p>
            <form method="post">
                <input type="hidden" name="unit_id" value="<?= h($editing['unit_id']) ?>">
                <input type="hidden" name="source_hash" value="<?= h($editing['source_hash']) ?>">
                <div class="editor-grid <?= !empty($editing['cpanel_target']) ? 'three' : '' ?>">
                    <div>
                        <label>Source</label>
                        <textarea readonly><?= h($editing['source']) ?></textarea>
                    </div>
                    <?php if (!empty($editing['cpanel_target'])): ?>
                        <div>
                            <label>
                                Traducao original cPanel
                                <?php if (($editing['cpanel_source_hash'] ?? '') !== $editing['source_hash']): ?>
                                    <span class="label-note">outra versao</span>
                                <?php endif; ?>
                            </label>
                            <textarea readonly><?= h($editing['cpanel_target']) ?></textarea>
                        </div>
                    <?php endif; ?>
                    <div>
                        <label>Traducao manual</label>
                        <textarea name="target"><?= h($editing['target'] ?? '') ?></textarea>
                    </div>
                </div>
                <div class="editor-actions">
                    <a class="button secondary" href="<?= h(current_url(['edit' => null, 'hash' => null])) ?>">Cancelar</a>
                    <button type="submit">Salvar revisao</button>
                </div>
            </form>
        </section>
    <?php endif; ?>

    <section class="table-wrap">
        <table>
            <thead>
            <tr>
                <th>ID</th>
                <th>Source</th>
                <th>Target atual</th>
                <th>Origem</th>
                <th>Escopo</th>
                <th></th>
            </tr>
            </thead>
            <tbody>
            <?php foreach ($result['items'] as $item): ?>
                <?php $origin = $item['origin'] ?: 'pending'; ?>
                <tr>
                    <td class="id-cell">
                        <?= h($item['unit_id']) ?><br>
                        <span class="muted"><?= h(substr($item['source_hash'], 0, 10)) ?></span>
                    </td>
                    <td class="source"><?= h(truncate_text($item['source'])) ?></td>
                    <td class="target"><?= h(truncate_text($item['target'] ?? '')) ?></td>
                    <td>
                        <span class="badge <?= h($origin) ?>"><?= h($origin) ?></span>
                        <?php if ((int) ($item['is_reviewed'] ?? 0) === 1): ?>
                            <span class="badge reviewed">reviewed</span>
                        <?php endif; ?>
                        <?php if (!empty($item['model'])): ?>
                            <div class="muted"><?= h($item['model']) ?></div>
                        <?php endif; ?>
                    </td>
                    <td>
                        <?= (int) $item['canonical'] === 1 ? 'canonical' : 'extra' ?>
                    </td>
                    <td>
                        <a class="button secondary" href="<?= h(current_url(['edit' => $item['unit_id'], 'hash' => $item['source_hash']])) ?>">Editar</a>
                    </td>
                </tr>
            <?php endforeach; ?>
            <?php if (!$result['items']): ?>
                <tr><td colspan="6" class="muted">Nenhum registro encontrado.</td></tr>
            <?php endif; ?>
            </tbody>
        </table>
        <div class="pagination">
            <span class="muted"><?= h((string) $result['total']) ?> registros · pagina <?= h((string) $page) ?> de <?= h((string) $totalPages) ?></span>
            <?php if ($page > 1): ?>
                <a class="button secondary" href="<?= h(current_url(['page' => $page - 1])) ?>">Anterior</a>
            <?php endif; ?>
            <?php if ($page < $totalPages): ?>
                <a class="button secondary" href="<?= h(current_url(['page' => $page + 1])) ?>">Proxima</a>
            <?php endif; ?>
        </div>
    </section>
</main>
</body>
</html>
