<?php
$jsonFile = 'data.json';
$data = file_exists($jsonFile) ? json_decode(file_get_contents($jsonFile), true) : ['categories' => [], 'articles' => []];

// 處理新增公告表單提交
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['action']) && $_POST['action'] === 'add_article') {
    $attachments = [];
    
    // 1. 自動建立 ./files/ 並搬移上傳檔案
    if (!empty($_FILES['art_file']['name']) && $_FILES['art_file']['error'] === UPLOAD_ERR_OK) {
        $uploadDir = './files/';
        if (!is_dir($uploadDir)) {
            mkdir($uploadDir, 0755, true);
        }
        $fileName = basename($_FILES['art_file']['name']);
        $targetPath = $uploadDir . $fileName;
        
        if (move_uploaded_file($_FILES['art_file']['tmp_name'], $targetPath)) {
            $attachments[] = ['name' => $fileName, 'url' => './files/' . $fileName];
        }
    }

    // 2. 更新資料並自動寫入 data.json
    array_unshift($data['articles'], [
        'id' => time(),
        'categoryId' => $_POST['art_category'],
        'title' => trim($_POST['art_title']),
        'isImportant' => isset($_POST['art_important']),
        'date' => date('Y-m-d'),
        'content' => trim($_POST['art_content']),
        'attachments' => $attachments
    ]);

    file_put_contents($jsonFile, json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
    header('Location: maintenance.php?status=success');
    exit;
}
?>
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>電子佈告欄 - 後台管理 (PHP版)</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 p-6 font-sans text-slate-800">
  <div class="max-w-4xl mx-auto bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-4">
    <h1 class="text-xl font-bold border-b pb-3">新增公告項目</h1>
    
    <form method="POST" enctype="multipart/form-data" class="space-y-4">
      <input type="hidden" name="action" value="add_article">
      
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <select name="art_category" class="border p-2 rounded text-sm" required>
          <?php foreach ($data['categories'] as $c): ?>
            <?php if ($c['id'] !== 'all'): ?>
              <option value="<?= htmlspecialchars($c['id']) ?>"><?= htmlspecialchars($c['name']) ?></option>
            <?php endif; ?>
          <?php endforeach; ?>
        </select>
        <input type="text" name="art_title" placeholder="公告標題" class="border p-2 rounded text-sm" required>
      </div>

      <label class="flex items-center space-x-2 text-sm font-bold text-amber-700 cursor-pointer">
        <input type="checkbox" name="art_important">
        <span>標示為【重要公告】</span>
      </label>

      <textarea name="art_content" placeholder="公告詳細內容..." class="w-full border p-2 rounded text-sm h-24" required></textarea>

      <div class="bg-slate-50 p-4 rounded-lg border space-y-1">
        <label class="block text-xs font-bold text-slate-700">選擇附加檔案 (自動儲存至 ./files/)</label>
        <input type="file" name="art_file" class="block w-full text-sm text-slate-500">
      </div>

      <button type="submit" class="bg-blue-600 hover:bg-blue-700 text-white font-bold px-6 py-2 rounded text-sm">
        發佈公告並自動儲存
      </button>
    </form>
  </div>
</body>
</html>