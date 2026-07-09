const state = {
  selectedFile: null,
  selectedBookId: null,
  books: [],
};

const els = {
  fileInput: document.querySelector("#fileInput"),
  dropZone: document.querySelector("#dropZone"),
  fileMeta: document.querySelector("#fileMeta"),
  filePreview: document.querySelector("#filePreview"),
  uploadButton: document.querySelector("#uploadButton"),
  refreshButton: document.querySelector("#refreshButton"),
  statusMessage: document.querySelector("#statusMessage"),
  bookList: document.querySelector("#bookList"),
  detailContent: document.querySelector("#detailContent"),
  exportButton: document.querySelector("#exportButton"),
  deleteButton: document.querySelector("#deleteButton"),
  searchForm: document.querySelector("#searchForm"),
  searchInput: document.querySelector("#searchInput"),
  searchButton: document.querySelector("#searchButton"),
  searchResults: document.querySelector("#searchResults"),
};

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN") : "-";
}

function setMessage(text, type) {
  els.statusMessage.textContent = text;
  els.statusMessage.className = "status-msg" + (type ? " " + type : "");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(detail || `请求失败：${response.status}`);
  }
  return payload;
}

function renderTags(tags) {
  if (!tags || !tags.length) return "";
  return `<div class="tag-row">${tags.map((t) => `<span class="badge">${escapeHtml(t)}</span>`).join("")}</div>`;
}

/* ============ 书籍列表 ============ */

function renderBookList() {
  if (!state.books.length) {
    els.bookList.innerHTML = '<p class="empty-list">暂无书籍，上传一本 TXT。</p>';
    return;
  }
  els.bookList.innerHTML = state.books
    .map(
      (b) => `
    <article class="book-card ${b.id === state.selectedBookId ? "active" : ""}" data-book-id="${escapeHtml(b.id)}">
      <h3>${escapeHtml(b.title)}</h3>
      <div class="book-meta">
        <span class="badge">${escapeHtml(b.status)}</span>
        <span>${escapeHtml(b.original_filename)}</span>
        <span>${formatTime(b.updated_at)}</span>
      </div>
    </article>`
    )
    .join("");
}

/* ============ 详情 ============ */

function renderDetail(book) {
  const summary = book.summary_json || {};
  const profile = summary.book_profile || {};
  const outline = Array.isArray(summary.outline) ? summary.outline.slice(0, 6) : [];
  const characters = Array.isArray(summary.character_cards) ? summary.character_cards.slice(0, 8) : [];
  const styleTags = Array.isArray(profile.style_tags) ? profile.style_tags : [];

  els.detailContent.innerHTML = `
    <div class="detail-grid">
      <div class="detail-item"><span>书名</span><strong>${escapeHtml(book.title)}</strong></div>
      <div class="detail-item"><span>状态</span><strong>${escapeHtml(book.status)} / ${escapeHtml(book.stage)}</strong></div>
      <div class="detail-item"><span>进度</span><strong>${book.progress}%</strong></div>
      <div class="detail-item"><span>Chunks</span><strong>${book.chunks_count}</strong></div>
      <div class="detail-item"><span>类型</span><strong>${escapeHtml(profile.genre || "-")}</strong></div>
      <div class="detail-item"><span>完成时间</span><strong>${formatTime(book.completed_at)}</strong></div>
    </div>

    <div class="summary-block">
      <h3>书籍画像</h3>
      <p>${escapeHtml(profile.background || "暂无画像摘要。")}</p>
      ${renderTags(styleTags)}

      <h3>章节大纲</h3>
      ${outline.length
        ? `<ol>${outline.map((o) => `<li><strong>${escapeHtml(o.chapter_title || `第 ${o.chapter_no} 章`)}</strong>：${escapeHtml(o.summary || "")}</li>`).join("")}</ol>`
        : "<p>暂无章节大纲。</p>"}

      <h3>角色卡</h3>
      ${characters.length
        ? `<ul>${characters.map((c) => `<li><strong>${escapeHtml(c.name)}</strong>：${escapeHtml(c.description || `出现 ${c.mentions ?? 0} 次`)}</li>`).join("")}</ul>`
        : "<p>暂无角色信息。</p>"}

      <h3>原始 JSON</h3>
      <pre class="json-view">${escapeHtml(JSON.stringify(summary, null, 2))}</pre>
    </div>`;
}

function setDetailEnabled(enabled) {
  els.exportButton.disabled = !enabled;
  els.deleteButton.disabled = !enabled;
  els.searchInput.disabled = !enabled;
  els.searchButton.disabled = !enabled;
}

/* ============ API 操作 ============ */

async function loadBooks() {
  els.bookList.innerHTML = '<p class="empty-list">加载中…</p>';
  state.books = await fetchJson("/api/books");
  renderBookList();
}

async function selectBook(bookId) {
  state.selectedBookId = bookId;
  renderBookList();
  els.searchResults.innerHTML = "";
  els.detailContent.innerHTML = '<p class="empty-list">加载中…</p>';
  setDetailEnabled(false);

  const book = await fetchJson(`/api/books/${encodeURIComponent(bookId)}`);
  renderDetail(book);
  setDetailEnabled(true);
}

async function uploadSelectedFile() {
  if (!state.selectedFile) return;

  const formData = new FormData();
  formData.append("file", state.selectedFile);

  els.uploadButton.disabled = true;
  setMessage("上传分析中…");
  try {
    const book = await fetchJson("/api/books/upload", { method: "POST", body: formData });
    setMessage(`分析完成：${book.title}`, "success");
    await loadBooks();
    await selectBook(book.id);
  } catch (err) {
    setMessage(err.message, "error");
  } finally {
    els.uploadButton.disabled = !state.selectedFile;
  }
}

/* ============ 文件预览 ============ */

async function previewFile(file) {
  state.selectedFile = file;
  els.uploadButton.disabled = !file;

  if (!file) {
    els.fileMeta.classList.add("hidden");
    els.filePreview.textContent = "文件预览会显示在这里。";
    els.filePreview.classList.add("empty");
    return;
  }

  els.fileMeta.classList.remove("hidden");
  els.fileMeta.textContent = `${file.name} · ${formatBytes(file.size)}`;
  const text = await file.text();
  els.filePreview.textContent = text.slice(0, 3000) || "文件内容为空。";
  els.filePreview.classList.toggle("empty", !text);
}

/* ============ 搜索 ============ */

async function searchSelectedBook(event) {
  event.preventDefault();
  if (!state.selectedBookId) return;

  const query = els.searchInput.value.trim();
  if (!query) {
    els.searchResults.innerHTML = '<p class="empty-list">输入关键词搜索。</p>';
    return;
  }

  els.searchButton.disabled = true;
  els.searchResults.innerHTML = '<p class="empty-list">搜索中…</p>';
  try {
    const data = await fetchJson(`/api/books/${encodeURIComponent(state.selectedBookId)}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: 8 }),
    });
    els.searchResults.innerHTML = data.hits.length
      ? data.hits
          .map(
            (h) => `
            <article class="hit-card">
              <h4>${escapeHtml(h.chapter_title)} · #${h.chunk_index}</h4>
              <p>${escapeHtml(h.summary)}</p>
              ${renderTags(h.tags)}
              <p class="hit-content">${escapeHtml(h.content)}</p>
            </article>`
          )
          .join("")
      : '<p class="empty-list">无匹配结果。</p>';
  } catch (err) {
    els.searchResults.innerHTML = `<p class="empty-list">${escapeHtml(err.message)}</p>`;
  } finally {
    els.searchButton.disabled = false;
  }
}

/* ============ 导出 / 删除 ============ */

async function exportSelectedBook() {
  if (!state.selectedBookId) return;
  const data = await fetchJson(`/api/books/${encodeURIComponent(state.selectedBookId)}/export`);
  const blob = new Blob([JSON.stringify(data.payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${state.selectedBookId}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

async function deleteSelectedBook() {
  if (!state.selectedBookId) return;
  if (!window.confirm("确定删除这本书及其索引和导出文件？")) return;

  await fetchJson(`/api/books/${encodeURIComponent(state.selectedBookId)}`, { method: "DELETE" });
  state.selectedBookId = null;
  setDetailEnabled(false);
  els.detailContent.innerHTML = '<div class="detail-empty">请选择一本书查看详情。</div>';
  els.searchResults.innerHTML = "";
  await loadBooks();
  setMessage("书籍已删除。", "success");
}

/* ============ 事件绑定 ============ */

els.fileInput.addEventListener("change", (e) => {
  previewFile(e.target.files[0] || null).catch((err) => setMessage(err.message, "error"));
});

els.dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  els.dropZone.classList.add("drag-over");
});
els.dropZone.addEventListener("dragleave", () => {
  els.dropZone.classList.remove("drag-over");
});
els.dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  els.dropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) previewFile(file).catch((err) => setMessage(err.message, "error"));
});

els.uploadButton.addEventListener("click", uploadSelectedFile);
els.refreshButton.addEventListener("click", () => {
  loadBooks().catch((err) => (els.bookList.innerHTML = `<p class="empty-list">${escapeHtml(err.message)}</p>`));
});

els.bookList.addEventListener("click", (e) => {
  const card = e.target.closest("[data-book-id]");
  if (!card) return;
  selectBook(card.dataset.bookId).catch((err) => {
    els.detailContent.innerHTML = `<div class="detail-empty">${escapeHtml(err.message)}</div>`;
    setDetailEnabled(false);
  });
});

els.searchForm.addEventListener("submit", searchSelectedBook);
els.exportButton.addEventListener("click", () => exportSelectedBook().catch((err) => setMessage(err.message, "error")));
els.deleteButton.addEventListener("click", () => deleteSelectedBook().catch((err) => setMessage(err.message, "error")));

loadBooks().catch((err) => (els.bookList.innerHTML = `<p class="empty-list">${escapeHtml(err.message)}</p>`));
