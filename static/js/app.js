(() => {
  const $ = (id) => document.getElementById(id);

  const topicEl = $("topic");
  const pubmedQueryEl = $("pubmedQuery");
  const maxResultsEl = $("maxResults");
  const askQuestionEl = $("askQuestion");
  const answerBox = $("answerBox");
  const resultListEl = $("resultList");
  const allResultListEl = $("allResultList");
  const previewBox = $("previewBox");
  const searchHint = $("searchHint");
  const loadingEl = $("loading");
  const errorBox = $("errorBox");
  const statusBadge = $("statusBadge");
  const modelProviderEl = $("modelProvider");
  const moreModal = $("moreModal");

  let latestKeywords = [];
  let showAllMode = false;
  let cachedFiles = [];
  /** 持久保存勾选的文件名，回答后仍保持选中 */
  const selectedSet = new Set();
  const PROVIDER_KEY = "pumed_llm_provider";
  /** 最近一次问答，供写入 Notion（避免输入框被清空后丢失） */
  let lastQa = { question: "", answer: "", sources: [] };
  let lastStudyQa = { question: "", answer: "", source: "" };
  let lastDeepThink = { prompt: "", answer: "", sources: [] };

  function getProvider() {
    return (modelProviderEl && modelProviderEl.value) || "deepseek";
  }

  function showTruncationStatus(data) {
    if (!statusBadge || !data) return;
    if (data.truncated) {
      const n = Number(data.uploaded_chars) || Number(data.max_chars) || 0;
      statusBadge.textContent = `文本被截断，成功上传 ${n.toLocaleString()} 字符`;
      statusBadge.className = "status bad";
      return;
    }
    refreshHealth().catch(() => {});
  }

  function withProvider(body = {}) {
    return { ...body, provider: getProvider() };
  }

  function getAnswerText(el) {
    if (!el) return "";
    if (el.querySelector(".placeholder")) return "";
    return (el.innerText || el.textContent || "").trim();
  }

  async function copyAnswer(el, btn) {
    showError("");
    const text = getAnswerText(el);
    if (!text) return showError("暂无回答内容可复制");
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      if (btn) {
        const prev = btn.textContent;
        btn.textContent = "已复制";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = prev;
          btn.classList.remove("copied");
        }, 1500);
      }
    } catch (err) {
      showError(err.message || "复制失败，请手动选择文本复制");
    }
  }

  function setLoading(on) {
    loadingEl.classList.toggle("hidden", !on);
    document.querySelectorAll(".btn").forEach((btn) => {
      btn.disabled = on;
    });
  }

  function showError(msg) {
    if (!msg) {
      errorBox.classList.add("hidden");
      errorBox.textContent = "";
      return;
    }
    errorBox.textContent = msg;
    errorBox.classList.remove("hidden");
  }

  function switchPage(name) {
    document.querySelectorAll(".page-tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.page === name);
    });
    document.querySelectorAll(".page-panel").forEach((p) => {
      p.classList.toggle("active", p.id === `page-${name}`);
    });
    if (name === "qa") {
      refreshFiles()
        .then(() => loadSavedAbstractQa())
        .catch(() => {});
    }
    if (name === "study") {
      refreshStudyPdfs().catch(() => {});
    }
  }

  document.querySelectorAll(".page-tab").forEach((tab) => {
    tab.addEventListener("click", () => switchPage(tab.dataset.page));
  });

  /** Enter = submit, Shift+Enter = newline */
  function bindEnterSubmit(textarea, handler) {
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handler();
      }
    });
  }

  async function api(path, options = {}, timeoutMs = 130000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        signal: controller.signal,
        ...options,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `请求失败 (${res.status})`);
      }
      return data;
    } catch (err) {
      if (err && err.name === "AbortError") {
        throw new Error("请求超时：模型响应过慢或网络异常，请重试");
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function syncSelectedFromDom(root = resultListEl) {
    root.querySelectorAll('input[type="checkbox"]').forEach((el) => {
      if (el.checked) selectedSet.add(el.value);
      else selectedSet.delete(el.value);
    });
  }

  /** 仅从当前可见列表同步勾选，避免隐藏的「更多」列表残留状态覆盖选择 */
  function syncVisibleSelections() {
    syncSelectedFromDom(resultListEl);
    if (!moreModal.classList.contains("hidden")) {
      syncSelectedFromDom(allResultListEl);
    }
  }

  /** 问答/删除时使用的最终勾选列表 */
  function getChosenFilenames() {
    syncVisibleSelections();
    return [...selectedSet];
  }

  function renderFileList(files, root) {
    if (!files.length) {
      root.innerHTML = "<li><span class='item-meta'>暂无本地检索结果</span></li>";
      return;
    }
    root.innerHTML = files
      .map((f, idx) => {
        const title = escapeHtml(f.label || f.filename);
        const meta = escapeHtml(
          `${f.date || ""}${f.count != null ? ` · ${f.count} 篇` : ""} · ${f.filename}`
        );
        const id = `chk-${root.id || "list"}-${idx}`;
        const checked = selectedSet.has(f.filename) ? " checked" : "";
        const activeCls = selectedSet.has(f.filename) ? " active" : "";
        return `<li class="${activeCls.trim()}" data-filename="${escapeHtml(f.filename)}">
          <input id="${id}" type="checkbox" value="${escapeHtml(f.filename)}" data-filepath="${escapeHtml(f.filepath)}"${checked} />
          <label for="${id}">
            <div class="item-title">${title}</div>
            <div class="item-meta">${meta}</div>
          </label>
        </li>`;
      })
      .join("");

    root.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) selectedSet.add(cb.value);
        else selectedSet.delete(cb.value);
        // 同步问答主列表与「更多」列表中的同名勾选框（不含全文学习 PDF）
        [resultListEl, allResultListEl].forEach((list) => {
          list.querySelectorAll('input[type="checkbox"]').forEach((other) => {
            if (other !== cb && other.value === cb.value) {
              other.checked = cb.checked;
            }
          });
        });
        updatePreview();
        loadSavedAbstractQa().catch(() => {});
      });
    });
  }

  function formatSavedAbstractQa(items, filenames) {
    if (!items || !items.length) return "";
    const src = (filenames || []).join("；");
    const head = src ? `【基于勾选】${src}\n\n` : "";
    const body = items
      .map((it, idx) => {
        const q = (it.question || "").trim();
        const a = (it.answer || "").trim();
        const when = it.saved_at ? `\n（保存于 ${it.saved_at}）` : "";
        return `【问答 ${idx + 1}】${when}\n【问题】\n${q}\n\n【回答】\n${a}`;
      })
      .join("\n\n" + "—".repeat(24) + "\n\n");
    return head + body;
  }

  function renderAbstractAnswerBox(text) {
    const t = (text || "").trim();
    if (!t) {
      answerBox.innerHTML =
        "<p class='placeholder'>显示回答的内容；保存后再次勾选相同摘要可查看既往问答</p>";
      return;
    }
    answerBox.textContent = t;
  }

  async function loadSavedAbstractQa(filenames) {
    const names = filenames || getChosenFilenames();
    if (!names.length) {
      // 无勾选时不覆盖「刚检索完成」等提示，仅在已有内容且非占位时也不强清
      if (!answerBox.querySelector(".placeholder") && !(lastQa.answer || "").trim()) {
        renderAbstractAnswerBox("");
      } else if (!lastQa.answer) {
        renderAbstractAnswerBox("");
      }
      return [];
    }
    try {
      const qs = names
        .map((n) => `files=${encodeURIComponent(n)}`)
        .join("&");
      const data = await api(`/api/abstract/saved-qa?${qs}`);
      const items = data.items || [];
      if (items.length) {
        renderAbstractAnswerBox(
          formatSavedAbstractQa(items, data.filenames || names)
        );
      } else {
        // 当前勾选集合无历史：若 lastQa 正好对应同一集合则保留，否则显示占位
        const same =
          lastQa.sources &&
          lastQa.sources.length === names.length &&
          [...lastQa.sources].sort().join("|") === [...names].sort().join("|") &&
          (lastQa.answer || "").trim();
        if (same) {
          const used = lastQa.sources.join("、");
          answerBox.textContent = `【基于勾选】${used}\n\n${lastQa.answer}`;
        } else {
          renderAbstractAnswerBox("");
        }
      }
      return items;
    } catch {
      return [];
    }
  }

  async function previewFile(filename) {
    if (!filename) return;
    try {
      const res = await fetch(`/api/files/${encodeURIComponent(filename)}`);
      if (!res.ok) throw new Error("读取失败");
      const text = await res.text();
      const head = text.slice(0, 2500);
      previewBox.textContent = head + (text.length > 2500 ? "\n…\n" : "");
    } catch (err) {
      previewBox.textContent = `预览失败：${err.message}`;
    }
  }

  function updatePreview() {
    const checked = [...selectedSet];
    resultListEl.querySelectorAll("li").forEach((li) => {
      li.classList.toggle("active", selectedSet.has(li.dataset.filename));
    });
    allResultListEl.querySelectorAll("li").forEach((li) => {
      li.classList.toggle("active", selectedSet.has(li.dataset.filename));
    });

    if (checked.length === 1) {
      previewFile(checked[0]);
      return;
    }
    if (checked.length > 1) {
      const items = cachedFiles.filter((f) => checked.includes(f.filename));
      previewBox.textContent = items
        .map(
          (f) =>
            `✓ ${f.label || f.filename}\n  ${f.date || ""} · ${f.count != null ? f.count + " 篇" : ""}\n  ${f.query ? "Query: " + f.query.slice(0, 120) : ""}`
        )
        .join("\n\n");
      return;
    }
    previewBox.innerHTML =
      "<p class='placeholder'>请选择近期生成的检索结果，显示检索说明和摘要预览</p>";
  }

  async function refreshFiles() {
    const limit = showAllMode ? undefined : 4;
    const url = limit ? `/api/files?limit=${limit}` : "/api/files";
    const data = await api(url);
    cachedFiles = data.files || [];
    const names = new Set(cachedFiles.map((f) => f.filename));
    // 若当前仅显示 4 条，仍保留未展示条目的勾选，避免误清
    // 仅在确认文件已删除时清理（全量列表时）
    if (!limit) {
      [...selectedSet].forEach((name) => {
        if (!names.has(name)) selectedSet.delete(name);
      });
    }
    renderFileList(cachedFiles, resultListEl);
    if (!moreModal.classList.contains("hidden")) {
      const all = await api("/api/files");
      const allFiles = all.files || [];
      const allNames = new Set(allFiles.map((f) => f.filename));
      [...selectedSet].forEach((name) => {
        if (!allNames.has(name)) selectedSet.delete(name);
      });
      renderFileList(allFiles, allResultListEl);
    }
    updatePreview();
  }

  async function refreshHealth() {
    try {
      const data = await api("/api/health");
      const providers = data.providers || [];
      if (modelProviderEl && providers.length) {
        modelProviderEl.innerHTML = providers
          .map((p) => {
            const mark = p.configured ? "" : "（未配置）";
            return `<option value="${escapeHtml(p.id)}" ${p.configured ? "" : "disabled"}>${escapeHtml(p.label)} · ${escapeHtml(p.model)}${mark}</option>`;
          })
          .join("");
        const saved = localStorage.getItem(PROVIDER_KEY);
        const pick =
          (saved && providers.find((p) => p.id === saved && p.configured)?.id) ||
          data.default_provider ||
          providers.find((p) => p.configured)?.id ||
          providers[0].id;
        modelProviderEl.value = pick;
      }
      const cur = providers.find((p) => p.id === getProvider()) || {};
      if (cur.configured || data.configured) {
        statusBadge.textContent = `已配置 · ${cur.model || data.model || getProvider()}`;
        statusBadge.className = "status ok";
      } else {
        statusBadge.textContent = "未配置 API Key";
        statusBadge.className = "status bad";
      }
    } catch {
      statusBadge.textContent = "服务未就绪";
      statusBadge.className = "status bad";
    }
  }

  if (modelProviderEl) {
    modelProviderEl.addEventListener("change", () => {
      localStorage.setItem(PROVIDER_KEY, getProvider());
      refreshHealth().catch(() => {});
    });
  }

  async function confirmTopic() {
    showError("");
    const topic = topicEl.value.trim();
    if (!topic) return showError("请输入检索内容");
    setLoading(true);
    try {
      const q = await api("/api/build-query", {
        method: "POST",
        body: JSON.stringify(withProvider({ question: topic })),
      });
      latestKeywords = q.keywords || [];
      pubmedQueryEl.value = q.pubmed_query || "";
      const kw = latestKeywords.length ? latestKeywords.slice(0, 2).join(" · ") : "（无）";
      searchHint.textContent =
        `关键词：${kw}\n思路：${q.rationale || "（无）"}\n请核对检索式后按回车或点击「开始检索」。`;
      if (!askQuestionEl.value.trim()) askQuestionEl.value = topic;
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function startSearch() {
    showError("");
    const pubmedQuery = pubmedQueryEl.value.trim();
    if (!pubmedQuery) return showError("请先生成或填写 PubMed 检索式");
    setLoading(true);
    try {
      const data = await api("/api/search", {
        method: "POST",
        body: JSON.stringify({
          pubmed_query: pubmedQuery,
          max_results: Number(maxResultsEl.value) || 1500,
          keywords: latestKeywords.slice(0, 2),
        }),
      });
      const projectLabel = data.project_name ? `项目 ${data.project_name}` : "本地";
      searchHint.textContent =
        `检索完成：命中 ${data.count} 篇，${projectLabel}\n摘要已保存 ${data.filename || ""}，全文 PDF 将写入该项目的 _full_pdf。\n可切换到「摘要问答」页面提问。`;
      showAllMode = false;
      // 新检索：仅勾选本次结果，避免仍带着上一次文件去问答
      selectedSet.clear();
      if (data.filename) selectedSet.add(data.filename);
      await refreshFiles();
      switchPage("qa");
      answerBox.innerHTML = "<p class='placeholder'>检索已完成。勾选右侧结果后提问即可。</p>";
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function askConfirm() {
    showError("");
    const question = askQuestionEl.value.trim();
    if (!question) return showError("请输入提问内容");
    const chosen = getChosenFilenames();
    if (!chosen.length) return showError("请先勾选至少一个检索结果");
    setLoading(true);
    try {
      // 问答开启深度推理，可能较慢（最长约 10 分钟）
      const data = await api(
        "/api/answer",
        {
          method: "POST",
          body: JSON.stringify(
            withProvider({
              question,
              filepaths: chosen,
            })
          ),
        },
        620000
      );
      const used = (data.used_files || chosen).join("、");
      const answerText = data.answer || "";
      answerBox.textContent = `【基于勾选】${used}\n\n${answerText}`;
      lastQa = {
        question,
        answer: answerText,
        sources: data.used_files || chosen,
      };
      showTruncationStatus(data);
      updatePreview();
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function saveAbstractAnswer() {
    showError("");
    const sources =
      lastQa.sources && lastQa.sources.length
        ? lastQa.sources
        : getChosenFilenames();
    const question =
      (lastQa.question || "").trim() || askQuestionEl.value.trim();
    const answer = (lastQa.answer || "").trim();
    if (!sources.length) return showError("请先勾选摘要并完成一次问答");
    if (!question) return showError("没有可保存的问题（请先提问并得到回答）");
    if (!answer) return showError("没有可保存的回答（请先提问并得到回答）");
    setLoading(true);
    try {
      const data = await api("/api/abstract/saved-qa", {
        method: "POST",
        body: JSON.stringify({
          filenames: sources,
          question,
          answer,
        }),
      });
      const items = data.items || [];
      renderAbstractAnswerBox(
        formatSavedAbstractQa(items, data.filenames || sources)
      );
      const btn = $("btnSaveAnswer");
      if (btn) {
        const prev = btn.textContent;
        btn.textContent = "已保存";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = prev;
          btn.classList.remove("copied");
        }, 1500);
      }
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  $("btnSearchConfirm").addEventListener("click", confirmTopic);
  $("btnSearchCancel").addEventListener("click", () => {
    showError("");
    topicEl.value = "";
    searchHint.textContent = "";
  });
  $("btnStartSearch").addEventListener("click", startSearch);

  $("btnAskConfirm").addEventListener("click", askConfirm);
  $("btnAskCancel").addEventListener("click", () => {
    askQuestionEl.value = "";
  });

  bindEnterSubmit(topicEl, confirmTopic);
  bindEnterSubmit(pubmedQueryEl, startSearch);
  bindEnterSubmit(askQuestionEl, askConfirm);

  $("btnMoreFiles").addEventListener("click", async () => {
    showError("");
    try {
      const data = await api("/api/files");
      renderFileList(data.files || [], allResultListEl);
      moreModal.classList.remove("hidden");
      updatePreview();
      loadSavedAbstractQa().catch(() => {});
    } catch (err) {
      showError(err.message);
    }
  });

  function closeMore() {
    // 关闭前先把弹窗勾选同步进 selectedSet
    if (!moreModal.classList.contains("hidden")) {
      syncSelectedFromDom(allResultListEl);
    }
    moreModal.classList.add("hidden");
    renderFileList(cachedFiles, resultListEl);
    updatePreview();
    loadSavedAbstractQa().catch(() => {});
  }

  $("btnCloseMore").addEventListener("click", closeMore);
  $("moreBackdrop").addEventListener("click", closeMore);

  $("btnDeleteFiles").addEventListener("click", async () => {
    showError("");
    const chosen = getChosenFilenames();
    if (!chosen.length) return showError("请先勾选要删除的条目");
    if (!confirm(`确定删除选中的 ${chosen.length} 个文件？此操作不可恢复。`)) return;
    setLoading(true);
    try {
      const data = await api("/api/files/delete", {
        method: "POST",
        body: JSON.stringify({ filenames: chosen }),
      });
      if (data.errors && data.errors.length) {
        showError(`部分删除失败：${data.errors.join("; ")}`);
      }
      (data.deleted || []).forEach((name) => selectedSet.delete(name));
      await refreshFiles();
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  });

  // ---------- 全文学习（文件夹对话框 + PDF.js + 基于文献问答） ----------
  const studyFolderHint = $("studyFolderHint");
  const pdfListEl = $("pdfList");
  const pdfViewer = $("pdfViewer");
  const studyAnswerBox = $("studyAnswerBox");
  const studyAskQuestionEl = $("studyAskQuestion");
  const folderPicker = $("folderPicker");
  const deepAnswerBox = $("deepAnswerBox");
  const deepPromptEl = $("deepPrompt");
  const deepPackListEl = $("deepPackList");
  let currentPdf = "";
  let pdfDoc = null;
  let cachedPdfs = [];
  /** 全文学习勾选（与问答 selectedSet 分离） */
  const studySelectedSet = new Set();
  /** 已发送到深度思考的文献包 */
  let deepPack = [];
  /** 当前全文学习所在项目，切换项目时清空勾选 */
  let studyFolderKey = "";

  if (window.pdfjsLib) {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  }

  function syncPdfSelectAll() {
    const box = $("pdfSelectAll");
    if (!box) return;
    const names = cachedPdfs.map((f) => f.filename);
    if (!names.length) {
      box.checked = false;
      box.indeterminate = false;
      box.disabled = true;
      return;
    }
    box.disabled = false;
    const selected = names.filter((name) => studySelectedSet.has(name)).length;
    box.checked = selected === names.length;
    box.indeterminate = selected > 0 && selected < names.length;
  }

  function renderPdfList(pdfs) {
    cachedPdfs = pdfs || [];
    if (!pdfs.length) {
      pdfListEl.innerHTML = "<li><span class='pdf-size'>该文件夹下没有 PDF</span></li>";
      syncPdfSelectAll();
      return;
    }
    pdfListEl.innerHTML = pdfs
      .map((f, idx) => {
        const active = f.filename === currentPdf ? " active" : "";
        const checked = studySelectedSet.has(f.filename);
        const checkedCls = checked ? " checked" : "";
        const kb = Math.max(1, Math.round((f.size || 0) / 1024));
        const id = `pdf-chk-${idx}`;
        return `<li class="${(active + checkedCls).trim()}" data-filename="${escapeHtml(f.filename)}">
          <input id="${id}" type="checkbox" value="${escapeHtml(f.filename)}"${checked ? " checked" : ""} />
          <div class="pdf-name" data-open="${escapeHtml(f.filename)}">
            ${escapeHtml(f.filename)}
            <span class="pdf-size">${kb} KB</span>
          </div>
        </li>`;
      })
      .join("");

    pdfListEl.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", () => {
        if (cb.checked) studySelectedSet.add(cb.value);
        else studySelectedSet.delete(cb.value);
        const li = cb.closest("li");
        if (li) li.classList.toggle("checked", cb.checked);
        syncPdfSelectAll();
      });
    });
    syncPdfSelectAll();
    pdfListEl.querySelectorAll(".pdf-name[data-open]").forEach((el) => {
      el.addEventListener("click", () => openPdf(el.dataset.open));
    });
  }

  function renderDeepPackList() {
    if (!deepPackListEl) return;
    if (!deepPack.length) {
      deepPackListEl.innerHTML =
        "<li><span class='item-meta'>尚未打包文献，请先在「全文学习」勾选并发送</span></li>";
      return;
    }
    deepPackListEl.innerHTML = deepPack
      .map((f) => {
        const kb = Math.max(1, Math.round((f.size || 0) / 1024));
        return `<li class="active" data-filename="${escapeHtml(f.filename)}">
          <label>
            <div class="item-title">${escapeHtml(f.filename)}</div>
            <div class="item-meta">${kb} KB</div>
          </label>
        </li>`;
      })
      .join("");
  }

  function sendToDeepThink() {
    showError("");
    const chosen = [...studySelectedSet];
    if (!cachedPdfs.length) {
      return showError("请先打开包含 PDF 的文件夹");
    }
    if (!chosen.length) {
      return showError("请先勾选至少一篇文献");
    }
    deepPack = cachedPdfs
      .filter((f) => chosen.includes(f.filename))
      .map((f) => ({ filename: f.filename, size: f.size || 0 }));
    if (!deepPack.length) {
      return showError("勾选的文献不在当前文件夹中，请重新勾选");
    }
    renderDeepPackList();
    if (deepAnswerBox) {
      deepAnswerBox.innerHTML =
        "<p class='placeholder'>基于所选全文与你的思路，生成研究计划或文章初稿</p>";
    }
    switchPage("deep");
  }

  async function deepThinkConfirm() {
    showError("");
    const prompt = (deepPromptEl && deepPromptEl.value.trim()) || "";
    if (!prompt) return showError("请输入思路 / 要求");
    if (!deepPack.length) {
      return showError("请先在「全文学习」勾选文献并发送到深度思考");
    }
    setLoading(true);
    try {
      const data = await api(
        "/api/deep-think",
        {
          method: "POST",
          body: JSON.stringify(
            withProvider({
              prompt,
              filenames: deepPack.map((f) => f.filename),
            })
          ),
        },
        620000
      );
      const answerText = data.answer || "";
      if (deepAnswerBox) deepAnswerBox.textContent = answerText;
      lastDeepThink = {
        prompt,
        answer: answerText,
        sources: deepPack.map((f) => f.filename),
      };
      showTruncationStatus(data);
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function appendCitationBlock(answer, sources) {
    const text = (answer || "").trim();
    const list = (sources || []).filter(Boolean);
    if (!list.length) return text;
    const already =
      /参考文献|引文来源|【引文/.test(text) &&
      list.every((name) => text.includes(name));
    if (already) return text;
    const lines = list.map((name, i) => `[${i + 1}] ${name}`);
    return `${text}\n\n【引文来源】\n${lines.join("\n")}`;
  }

  async function refreshStudyPdfs() {
    const data = await api("/api/study/pdfs");
    const key = data.project || data.folder || "";
    const pdfs = data.pdfs || [];
    if (key !== studyFolderKey) {
      studyFolderKey = key;
      studySelectedSet.clear();
      if (currentPdf && !pdfs.some((f) => f.filename === currentPdf)) {
        currentPdf = "";
        pdfViewer.innerHTML = "<p class='placeholder'>请从左侧选择 PDF 文件</p>";
        studyAnswerBox.innerHTML = "<p class='placeholder'>回答</p>";
      }
    }
    const names = new Set(pdfs.map((f) => f.filename));
    [...studySelectedSet].forEach((name) => {
      if (!names.has(name)) studySelectedSet.delete(name);
    });
    if (studyFolderHint) {
      studyFolderHint.textContent = `当前项目：${data.folder || "未命名"}（${data.count || 0} 个 PDF）`;
    }
    renderPdfList(pdfs);
  }

  async function uploadPdfFiles(fileList, folderName) {
    const form = new FormData();
    form.append("folder_name", folderName || "已选文件夹");
    let count = 0;
    for (const file of fileList) {
      const name = (file.name || "").toLowerCase();
      if (!name.endsWith(".pdf") && file.type !== "application/pdf") continue;
      form.append("files", file, file.name);
      count += 1;
    }
    if (!count) throw new Error("所选文件夹中没有 PDF 文件");

    const res = await fetch("/api/study/upload", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `上传失败 (${res.status})`);
    }
    return data;
  }

  async function pickFolderViaDirectoryPicker() {
    if (!window.showDirectoryPicker) return null;
    const dirHandle = await window.showDirectoryPicker({ mode: "read" });
    const files = [];
    for await (const entry of dirHandle.values()) {
      if (entry.kind !== "file") continue;
      const file = await entry.getFile();
      const n = file.name.toLowerCase();
      if (n.endsWith(".pdf") || file.type === "application/pdf") {
        files.push(file);
      }
    }
    return { files, name: dirHandle.name || "已选文件夹" };
  }

  async function openFolderDialog() {
    showError("");
    try {
      const picked = await pickFolderViaDirectoryPicker();
      if (picked) {
        setLoading(true);
        try {
          const data = await uploadPdfFiles(picked.files, picked.name);
          studyFolderKey = data.project || data.folder || "";
          studyFolderHint.textContent = `当前项目：${data.folder}（${data.count} 个 PDF）`;
          currentPdf = "";
          studySelectedSet.clear();
          pdfViewer.innerHTML = "<p class='placeholder'>请从左侧选择 PDF 文件</p>";
          studyAnswerBox.innerHTML = "<p class='placeholder'>回答</p>";
          renderPdfList(data.pdfs || []);
        } finally {
          setLoading(false);
        }
        return;
      }
      folderPicker.value = "";
      folderPicker.click();
    } catch (err) {
      if (err && err.name === "AbortError") return;
      folderPicker.value = "";
      folderPicker.click();
    }
  }

  folderPicker.addEventListener("change", async () => {
    const files = [...(folderPicker.files || [])];
    if (!files.length) return;
    showError("");
    setLoading(true);
    try {
      const rel = files[0].webkitRelativePath || files[0].name;
      const folderName = rel.includes("/") ? rel.split("/")[0] : "已选文件夹";
      const data = await uploadPdfFiles(files, folderName);
      studyFolderKey = data.project || data.folder || "";
      studyFolderHint.textContent = `当前项目：${data.folder}（${data.count} 个 PDF）`;
      currentPdf = "";
      studySelectedSet.clear();
      pdfViewer.innerHTML = "<p class='placeholder'>请从左侧选择 PDF 文件</p>";
      studyAnswerBox.innerHTML = "<p class='placeholder'>回答</p>";
      renderPdfList(data.pdfs || []);
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  });

  async function renderPdfPages(pdf) {
    pdfViewer.innerHTML = "";
    // 按中间列可视宽度全宽渲染
    const padding = 20;
    const targetWidth = Math.max(320, pdfViewer.clientWidth - padding);

    for (let num = 1; num <= pdf.numPages; num += 1) {
      const page = await pdf.getPage(num);
      const base = page.getViewport({ scale: 1 });
      const scale = targetWidth / base.width;
      const viewport = page.getViewport({ scale });

      const pageDiv = document.createElement("div");
      pageDiv.className = "pdf-page";
      pageDiv.style.width = `${viewport.width}px`;
      pageDiv.style.height = `${viewport.height}px`;

      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      ctx.setTransform(outputScale, 0, 0, outputScale, 0, 0);
      pageDiv.appendChild(canvas);

      const textLayerDiv = document.createElement("div");
      textLayerDiv.className = "textLayer";
      textLayerDiv.style.width = `${viewport.width}px`;
      textLayerDiv.style.height = `${viewport.height}px`;
      textLayerDiv.style.setProperty("--scale-factor", String(scale));
      pageDiv.appendChild(textLayerDiv);

      pdfViewer.appendChild(pageDiv);

      await page.render({ canvasContext: ctx, viewport }).promise;

      const textContent = await page.getTextContent();
      await pdfjsLib.renderTextLayer({
        textContentSource: textContent,
        container: textLayerDiv,
        viewport,
        textDivs: [],
      }).promise;
    }
  }

  function formatSavedStudyQa(items) {
    if (!items || !items.length) return "";
    return items
      .map((it, idx) => {
        const q = (it.question || "").trim();
        const a = (it.answer || "").trim();
        const when = it.saved_at ? `\n（保存于 ${it.saved_at}）` : "";
        return `【问答 ${idx + 1}】${when}\n【问题】\n${q}\n\n【回答】\n${a}`;
      })
      .join("\n\n" + "—".repeat(24) + "\n\n");
  }

  function renderStudyAnswerBox(text) {
    const t = (text || "").trim();
    if (!t) {
      studyAnswerBox.innerHTML = "<p class='placeholder'>回答</p>";
      return;
    }
    studyAnswerBox.textContent = t;
  }

  async function loadSavedStudyQa(filename) {
    if (!filename) {
      renderStudyAnswerBox("");
      return [];
    }
    try {
      const data = await api(`/api/study/saved-qa/${encodeURIComponent(filename)}`);
      const items = data.items || [];
      if (items.length) {
        renderStudyAnswerBox(formatSavedStudyQa(items));
      } else {
        renderStudyAnswerBox("");
      }
      return items;
    } catch {
      renderStudyAnswerBox("");
      return [];
    }
  }

  async function openPdf(filename) {
    showError("");
    if (!window.pdfjsLib) {
      return showError("PDF.js 未加载，请检查网络或刷新页面");
    }
    currentPdf = filename;
    lastStudyQa = { question: "", answer: "", source: filename };
    pdfListEl.querySelectorAll("li").forEach((li) => {
      li.classList.toggle("active", li.dataset.filename === filename);
      li.classList.toggle("checked", studySelectedSet.has(li.dataset.filename));
    });
    setLoading(true);
    pdfViewer.innerHTML = "<p class='placeholder'>正在加载 PDF…</p>";
    studyAnswerBox.innerHTML = "<p class='placeholder'>回答</p>";
    try {
      const url = `/api/study/file/${encodeURIComponent(filename)}`;
      pdfDoc = await pdfjsLib.getDocument({ url, withCredentials: false }).promise;
      await renderPdfPages(pdfDoc);
      studyFolderHint.textContent = `${filename} · ${pdfDoc.numPages} 页`;
      await loadSavedStudyQa(filename);
    } catch (err) {
      showError(err.message || String(err));
      pdfViewer.innerHTML = `<p class="placeholder">打开失败：${escapeHtml(err.message || String(err))}</p>`;
    } finally {
      setLoading(false);
    }
  }

  async function studyAskConfirm() {
    showError("");
    const question = studyAskQuestionEl.value.trim();
    if (!question) return showError("请输入提问内容");
    if (!currentPdf) return showError("请先打开一篇 PDF 文献");
    setLoading(true);
    try {
      const data = await api(
        "/api/study/ask",
        {
          method: "POST",
          body: JSON.stringify(
            withProvider({
              question,
              filename: currentPdf,
            })
          ),
        },
        620000
      );
      const answerText = data.answer || "";
      studyAnswerBox.textContent = answerText;
      lastStudyQa = {
        question,
        answer: answerText,
        source: currentPdf || "",
      };
      showTruncationStatus(data);
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function saveStudyAnswer() {
    showError("");
    const filename = currentPdf || lastStudyQa.source || "";
    const question =
      (lastStudyQa.question || "").trim() || studyAskQuestionEl.value.trim();
    const answer = (lastStudyQa.answer || "").trim();
    if (!filename) return showError("请先打开一篇 PDF 文献");
    if (!question) return showError("没有可保存的问题（请先提问并得到回答）");
    if (!answer) return showError("没有可保存的回答（请先提问并得到回答）");
    setLoading(true);
    try {
      const data = await api("/api/study/saved-qa", {
        method: "POST",
        body: JSON.stringify({ filename, question, answer }),
      });
      const items = data.items || [];
      renderStudyAnswerBox(formatSavedStudyQa(items));
      const btn = $("btnSaveStudyAnswer");
      if (btn) {
        const prev = btn.textContent;
        btn.textContent = "已保存";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = prev;
          btn.classList.remove("copied");
        }, 1500);
      }
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  $("pdfSelectAll")?.addEventListener("change", () => {
    const on = $("pdfSelectAll").checked;
    cachedPdfs.forEach((f) => {
      if (on) studySelectedSet.add(f.filename);
      else studySelectedSet.delete(f.filename);
    });
    renderPdfList(cachedPdfs);
  });

  $("btnOpenFolder").addEventListener("click", openFolderDialog);
  $("btnSendDeepThink")?.addEventListener("click", sendToDeepThink);
  $("btnStudyAskConfirm").addEventListener("click", studyAskConfirm);
  $("btnStudyAskCancel").addEventListener("click", () => {
    studyAskQuestionEl.value = "";
  });
  bindEnterSubmit(studyAskQuestionEl, studyAskConfirm);
  $("btnSaveStudyAnswer")?.addEventListener("click", saveStudyAnswer);

  $("btnDeepConfirm")?.addEventListener("click", deepThinkConfirm);
  $("btnDeepCancel")?.addEventListener("click", () => {
    if (deepPromptEl) deepPromptEl.value = "";
  });
  if (deepPromptEl) bindEnterSubmit(deepPromptEl, deepThinkConfirm);
  renderDeepPackList();

  const btnCopyAnswer = $("btnCopyAnswer");
  const btnCopyStudyAnswer = $("btnCopyStudyAnswer");
  const btnCopyDeepAnswer = $("btnCopyDeepAnswer");
  $("btnSaveAnswer")?.addEventListener("click", saveAbstractAnswer);
  if (btnCopyAnswer) {
    btnCopyAnswer.addEventListener("click", () => copyAnswer(answerBox, btnCopyAnswer));
  }
  if (btnCopyStudyAnswer) {
    btnCopyStudyAnswer.addEventListener("click", () =>
      copyAnswer(studyAnswerBox, btnCopyStudyAnswer)
    );
  }
  if (btnCopyDeepAnswer) {
    btnCopyDeepAnswer.addEventListener("click", () =>
      copyAnswer(deepAnswerBox, btnCopyDeepAnswer)
    );
  }

  const notionModal = $("notionModal");
  const notionTitle = $("notionTitle");
  const notionSummary = $("notionSummary");
  const notionLearnDate = $("notionLearnDate");
  const notionSourceName = $("notionSourceName");
  const notionSourceType = $("notionSourceType");
  const notionTags = $("notionTags");
  const notionStatus = $("notionStatus");
  const notionImportance = $("notionImportance");
  const notionUrl = $("notionUrl");
  const notionAnswer = $("notionAnswer");

  function todayISO() {
    const d = new Date();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${m}-${day}`;
  }

  function closeNotionModal() {
    if (notionModal) notionModal.classList.add("hidden");
  }

  function openNotionModal({ title, answer, sourceName, sourceType }) {
    showError("");
    if (!title.trim()) return showError("缺少提问内容，无法写入 Notion");
    if (!answer.trim()) return showError("缺少回答内容，无法写入 Notion");
    notionTitle.value = title;
    notionSummary.value = "";
    notionLearnDate.value = todayISO();
    notionSourceName.value = sourceName || "";
    notionSourceType.value = sourceType || "PubMed摘要";
    notionTags.value = "写作/科研";
    notionStatus.value = "待读";
    notionImportance.value = "⭐";
    notionUrl.value = "";
    notionAnswer.value = answer;
    notionModal.classList.remove("hidden");
  }

  function openNotionFromQa() {
    const question =
      (lastQa.question || "").trim() || askQuestionEl.value.trim();
    let answer = (lastQa.answer || "").trim();
    if (!answer) {
      const raw = getAnswerText(answerBox);
      const marker = "\n\n";
      const idx = raw.indexOf(marker);
      answer = idx >= 0 ? raw.slice(idx + marker.length).trim() : raw;
    }
    const sources =
      lastQa.sources && lastQa.sources.length
        ? lastQa.sources
        : getChosenFilenames();
    openNotionModal({
      title: question,
      answer,
      sourceName: sources.join("；"),
      sourceType: "PubMed摘要",
    });
  }

  function openNotionFromStudy() {
    const question =
      (lastStudyQa.question || "").trim() ||
      studyAskQuestionEl.value.trim();
    const answer =
      (lastStudyQa.answer || "").trim() || getAnswerText(studyAnswerBox);
    const source = lastStudyQa.source || currentPdf || "";
    openNotionModal({
      title: question,
      answer,
      sourceName: source,
      sourceType: "PDF全文",
    });
  }

  function openNotionFromDeep() {
    const question =
      (lastDeepThink.prompt || "").trim() ||
      (deepPromptEl && deepPromptEl.value.trim()) ||
      "";
    const rawAnswer =
      (lastDeepThink.answer || "").trim() || getAnswerText(deepAnswerBox);
    const sources =
      lastDeepThink.sources && lastDeepThink.sources.length
        ? lastDeepThink.sources
        : deepPack.map((f) => f.filename);
    const answer = appendCitationBlock(rawAnswer, sources);
    openNotionModal({
      title: question || "深度思考输出",
      answer,
      sourceName: sources.join("；"),
      sourceType: "PDF全文",
    });
  }

  async function confirmNotionExport() {
    showError("");
    const title = notionTitle.value.trim();
    const answer = notionAnswer.value.trim();
    if (!title) return showError("标题（提问）不能为空");
    if (!answer) return showError("回答正文不能为空");
    const tags = notionTags.value
      .split(/[,，;；]/)
      .map((t) => t.trim())
      .filter(Boolean);
    setLoading(true);
    try {
      const data = await api(
        "/api/notion/export",
        {
          method: "POST",
          body: JSON.stringify({
            title,
            question: title,
            answer,
            summary: notionSummary.value.trim(),
            learn_date: notionLearnDate.value.trim(),
            source_name: notionSourceName.value.trim(),
            source_type: notionSourceType.value.trim(),
            tags,
            status: notionStatus.value.trim(),
            importance: notionImportance.value.trim(),
            url: notionUrl.value.trim(),
          }),
        },
        120000
      );
      closeNotionModal();
      const url = data.page_url || "";
      if (url) {
        showError("");
        const open = window.confirm(`已写入 Notion。\n是否打开页面？\n${url}`);
        if (open) window.open(url, "_blank", "noopener,noreferrer");
      } else {
        alert("已写入 Notion");
      }
    } catch (err) {
      showError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const btnNotionAnswer = $("btnNotionAnswer");
  const btnNotionStudyAnswer = $("btnNotionStudyAnswer");
  const btnNotionDeepAnswer = $("btnNotionDeepAnswer");
  if (btnNotionAnswer) {
    btnNotionAnswer.addEventListener("click", openNotionFromQa);
  }
  if (btnNotionStudyAnswer) {
    btnNotionStudyAnswer.addEventListener("click", openNotionFromStudy);
  }
  if (btnNotionDeepAnswer) {
    btnNotionDeepAnswer.addEventListener("click", openNotionFromDeep);
  }
  $("btnCloseNotion")?.addEventListener("click", closeNotionModal);
  $("btnNotionCancel")?.addEventListener("click", closeNotionModal);
  $("notionBackdrop")?.addEventListener("click", closeNotionModal);
  $("btnNotionConfirm")?.addEventListener("click", confirmNotionExport);

  refreshHealth();
  refreshFiles().catch(() => {});
})();
